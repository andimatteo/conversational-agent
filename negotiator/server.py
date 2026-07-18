"""FastAPI app.

Two route families:
  /api/...          — the product API (Lovable dashboard talks to this)
  /agent-tools/...  — webhook tools the ElevenLabs agents call MID-CALL.

Honesty is architecture here: the negotiator agent has no way to state a
competing bid except get_competing_quotes, which reads the real quote DB.
It structurally cannot invent leverage.
"""
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import db
from .benchmarks import counterparty_pricing, evaluate_red_flags, market_range
from .config import personas, vertical
from .models import Company, Job, OutcomeIn, QuoteIn
from .report import build_report

app = FastAPI(title="The Negotiator")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# --------------------------------------------------------------------------
# Product API
# --------------------------------------------------------------------------
@app.post("/api/jobs")
def create_job():
    job = Job(id=db.new_id("job"), vertical=vertical()["meta"]["vertical"])
    db.put("jobs", job.id, job.model_dump())
    return job


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return _job(job_id)


@app.post("/api/jobs/{job_id}/documents")
async def upload_document(job_id: str, file: UploadFile):
    from .docparse import parse_document  # lazy: needs OPENAI_API_KEY
    job = _job(job_id)
    spec = parse_document(file.filename, await file.read())
    job["spec"] = {**spec, **{k: v for k, v in job["spec"].items() if v}}  # interview answers win
    job["spec_source"] = (job["spec_source"] + "+document").lstrip("+")
    db.put("jobs", job_id, job)
    return {"job": job, "parsed": spec}


@app.post("/api/jobs/{job_id}/confirm")
def confirm_spec(job_id: str):
    job = _job(job_id)
    missing = [f for f in vertical()["spec_schema"]["required"] if not job["spec"].get(f)]
    if missing:
        raise HTTPException(422, f"Spec incomplete, cannot confirm. Missing: {missing}")
    job["confirmed"] = True
    db.put("jobs", job_id, job)
    return job


@app.get("/api/jobs/{job_id}/market")
def market(job_id: str, city: str, state: str):
    """Real-world call-list discovery via Tavily (simulated personas stay the callable demo market)."""
    from .discovery import discover
    return {"discovered": discover(city, state), "note": "Demo calls run against the simulated personas."}


@app.get("/api/jobs/{job_id}/companies")
def companies(job_id: str):
    return db.where("companies", job_id=job_id)


@app.get("/api/jobs/{job_id}/quotes")
def quotes(job_id: str):
    return db.where("quotes", job_id=job_id)


@app.get("/api/jobs/{job_id}/calls")
def calls(job_id: str):
    return db.where("calls", job_id=job_id)


@app.get("/api/jobs/{job_id}/report")
def report(job_id: str):
    _job(job_id)
    return build_report(job_id)


# --------------------------------------------------------------------------
# Agent tools (ElevenLabs webhook tools) — called mid-call
# --------------------------------------------------------------------------
class JobRef(BaseModel):
    job_id: str


class CompanyRef(BaseModel):
    job_id: str
    company_id: str


class SpecIn(BaseModel):
    job_id: str
    spec: dict


@app.post("/agent-tools/get_job_spec")
def t_get_job_spec(ref: JobRef):
    job = _job(ref.job_id)
    if not job["confirmed"]:
        raise HTTPException(409, "Job spec not confirmed by the user yet — no calls allowed.")
    return {"spec": job["spec"]}


@app.post("/agent-tools/save_job_spec")
def t_save_job_spec(body: SpecIn):
    """Called by the Estimator at the end of the voice interview."""
    job = _job(body.job_id)
    job["spec"] = {**job["spec"], **body.spec}
    job["spec_source"] = ("interview+" + job["spec_source"]).rstrip("+") if job["spec_source"] else "interview"
    job["confirmed"] = False  # any spec change requires re-confirmation
    db.put("jobs", body.job_id, job)
    missing = [f for f in vertical()["spec_schema"]["required"] if not job["spec"].get(f)]
    return {"saved": True, "missing_required_fields": missing}


@app.post("/agent-tools/log_quote")
def t_log_quote(q: QuoteIn):
    job = _job(q.job_id)
    data = q.model_dump()
    data["red_flags"] = evaluate_red_flags(q, job["spec"])
    data["id"] = db.new_id("quote")
    data["conversation_id"] = _latest_call_field(q.job_id, q.company_id, "conversation_id")
    from datetime import datetime, timezone
    data["created_at"] = datetime.now(timezone.utc).isoformat()
    db.put("quotes", data["id"], data, job_id=q.job_id, company_id=q.company_id, phase=q.phase)
    return {"logged": True, "red_flags": data["red_flags"]}


@app.post("/agent-tools/log_call_outcome")
def t_log_outcome(o: OutcomeIn):
    call = _latest_call(o.job_id, o.company_id)
    if call is None:
        raise HTTPException(404, "No active call record for this job+company.")
    call.update(o.model_dump(exclude={"job_id", "company_id"}))
    db.put("calls", call["id"], call, job_id=o.job_id, company_id=o.company_id)
    return {"logged": True}


@app.post("/agent-tools/get_competing_quotes")
def t_competing_quotes(ref: CompanyRef):
    """THE honesty gate: the only source of competitive leverage that exists."""
    out = []
    for q in db.where("quotes", job_id=ref.job_id):
        if q["company_id"] == ref.company_id:
            continue
        co = db.get("companies", q["company_id"]) or {}
        out.append({"company": co.get("name", "?"), "total": q["total"], "binding": q["binding"],
                    "phase": q["phase"],
                    "line_items": [{"label": li["label"], "amount": li["amount"]} for li in q["line_items"]]})
    spec = _job(ref.job_id)["spec"]
    if spec.get("existing_quote"):  # from document intake: leverage the user already had
        eq = spec["existing_quote"]
        out.append({"company": eq.get("company", "prior written quote"), "total": eq.get("total"),
                    "binding": True, "phase": "document",
                    "line_items": eq.get("line_items", [])})
    return {"competing_quotes": out,
            "rules": "Cite ONLY these, with exact company names and figures. If empty, you have no competing bids and must not imply otherwise."}


@app.post("/agent-tools/get_benchmark")
def t_benchmark(ref: JobRef):
    return market_range(_job(ref.job_id)["spec"])


@app.post("/agent-tools/counterparty_pricing")
def t_counterparty_pricing(ref: CompanyRef):
    """The simulated company's private back office. Only counterparty agents have this tool."""
    co = db.get("companies", ref.company_id)
    if not co or not co.get("persona"):
        raise HTTPException(404, "Not a simulated company.")
    persona = next(p for p in personas() if p["id"] == co["persona"])
    return counterparty_pricing(persona, _job(ref.job_id)["spec"])


# --------------------------------------------------------------------------
def _job(job_id: str) -> dict:
    job = db.get("jobs", job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


def _latest_call(job_id: str, company_id: str) -> dict | None:
    rows = db.where("calls", job_id=job_id, company_id=company_id)
    return sorted(rows, key=lambda c: c.get("started_at", ""))[-1] if rows else None


def _latest_call_field(job_id: str, company_id: str, field: str) -> str:
    call = _latest_call(job_id, company_id)
    return (call or {}).get(field, "")
