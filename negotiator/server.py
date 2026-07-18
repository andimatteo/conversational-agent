"""FastAPI app.

Two route families:
  /api/...          — the product API (Lovable dashboard talks to this)
  /agent-tools/...  — webhook tools the ElevenLabs agents call MID-CALL.

Honesty is architecture here: the negotiator agent has no way to state a
competing bid except get_competing_quotes, which reads the real quote DB.
It structurally cannot invent leverage.
"""
from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import db
from .benchmarks import counterparty_pricing, evaluate_red_flags, market_range
from .config import personas, vertical
from .models import Company, Job, LearnedIn, OutcomeIn, QuoteIn
from .packs import list_packs, load_pack
from .report import build_report

app = FastAPI(title="The Negotiator")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# --------------------------------------------------------------------------
# Product API
# --------------------------------------------------------------------------
class JobCreate(BaseModel):
    vertical: str = ""              # defaults to the process VERTICAL pack
    area_code: str = ""             # defaults to the chosen sheet's own area


@app.post("/api/jobs")
def create_job(body: JobCreate | None = None):
    vname = (body.vertical if body and body.vertical else vertical()["meta"]["vertical"])
    try:
        pack = load_pack(vname, body.area_code if body else "")
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    area = (body.area_code if body and body.area_code else pack["meta"].get("area_code", ""))
    job = Job(id=db.new_id("job"), vertical=vname, area_code=area)
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
    missing = [f for f in _pack(job)["spec_schema"]["required"] if not job["spec"].get(f)]
    if missing:
        raise HTTPException(422, f"Spec incomplete, cannot confirm. Missing: {missing}")
    job["confirmed"] = True
    db.put("jobs", job_id, job)
    return job


# --------------------------------------------------------------------------
# Domain sheets + the intake form (base questions + area-learned questions)
# --------------------------------------------------------------------------
@app.get("/api/verticals")
def verticals():
    """Every domain/area sheet on disk. Swapping domain = picking another one."""
    return list_packs()


class GenerateIn(BaseModel):
    vertical: str
    area_code: str = ""
    notes: str = ""
    force: bool = False


@app.post("/api/verticals/generate")
def generate_vertical(body: GenerateIn):
    """AI-write the sheet for a new domain/area (validated before saving)."""
    from .packgen import generate_pack  # lazy: needs OPENAI_API_KEY
    try:
        path, pack = generate_pack(body.vertical, body.area_code, body.notes, force=body.force)
    except FileExistsError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"file": path.name, "vertical": pack["meta"]["vertical"],
            "area_code": pack["meta"].get("area_code", ""),
            "display_name": pack["meta"]["display_name"]}


@app.get("/api/intake-form")
def intake_form(vertical_name: str = Query("", alias="vertical"), area_code: str = ""):
    """What the web form renders: base questions from the sheet + questions
    learned from previous calls in this area."""
    vname = vertical_name or vertical()["meta"]["vertical"]
    try:
        pack = load_pack(vname, area_code)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    area = area_code or pack["meta"].get("area_code", "")
    return {"vertical": vname, "area_code": area,
            "display_name": pack["meta"]["display_name"],
            "spec_schema": pack["spec_schema"],
            "base_questions": pack["estimator_questions"],
            "learned_questions": _learned(vname, area)}


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
    missing = [f for f in _pack(job)["spec_schema"]["required"] if not job["spec"].get(f)]
    return {"saved": True, "missing_required_fields": missing}


@app.post("/agent-tools/get_intake_form")
def t_get_intake_form(ref: JobRef):
    """The Estimator's FIRST call: the full question list for this job's
    domain+area — base sheet questions plus everything learned from
    previous calls in the same area."""
    job = _job(ref.job_id)
    pack = _pack(job)
    return {"base_questions": pack["estimator_questions"],
            "learned_questions": _learned(job["vertical"], job.get("area_code", "")),
            "note": "Work through ALL base questions. Learned questions were discovered "
                    "on previous calls in this service area — ask them too."}


@app.post("/agent-tools/log_learned_questions")
def t_log_learned_questions(body: LearnedIn):
    """End-of-intake: persist NEW price-relevant questions this call surfaced.
    They join the intake form for every future job in the same (vertical, area),
    and are surfaced to the user on the job record."""
    from datetime import datetime, timezone
    job = _job(body.job_id)
    pack = _pack(job)
    vname, area = job["vertical"], job.get("area_code", "")
    known = {_norm(r["question"]): r for r in db.where("learned_questions", vertical=vname, area_code=area)}
    base_norms = {_norm(q) for q in pack["estimator_questions"]}

    added, already_known = [], []
    for lq in body.questions:
        n = _norm(lq.question)
        if not n:
            continue
        if n in base_norms:
            already_known.append(lq.question)
            continue
        if n in known:
            row = known[n]
            row["times_seen"] = row.get("times_seen", 1) + 1
            db.put("learned_questions", row["id"], row, vertical=vname, area_code=area)
            already_known.append(lq.question)
            continue
        row = {"id": db.new_id("lq"), "vertical": vname, "area_code": area,
               "question": lq.question.strip(), "why_it_matters": lq.why_it_matters.strip(),
               "source_job_id": job["id"], "times_seen": 1, "status": "active",
               "created_at": datetime.now(timezone.utc).isoformat()}
        db.put("learned_questions", row["id"], row, vertical=vname, area_code=area)
        known[n] = row
        added.append({"question": row["question"], "why_it_matters": row["why_it_matters"]})

    if added:  # surface to the user on the job itself
        job["discovered_questions"] = job.get("discovered_questions", []) + added
        db.put("jobs", job["id"], job)
    return {"logged": True, "added": added, "already_known": already_known}


@app.post("/agent-tools/log_quote")
def t_log_quote(q: QuoteIn):
    job = _job(q.job_id)
    data = q.model_dump()
    data["red_flags"] = evaluate_red_flags(q, job["spec"], _pack(job))
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
    job = _job(ref.job_id)
    return market_range(job["spec"], _pack(job))


@app.post("/agent-tools/counterparty_pricing")
def t_counterparty_pricing(ref: CompanyRef):
    """The simulated company's private back office. Only counterparty agents have this tool."""
    co = db.get("companies", ref.company_id)
    if not co or not co.get("persona"):
        raise HTTPException(404, "Not a simulated company.")
    persona = next(p for p in personas() if p["id"] == co["persona"])
    job = _job(ref.job_id)
    return counterparty_pricing(persona, job["spec"], _pack(job))


# --------------------------------------------------------------------------
def _job(job_id: str) -> dict:
    job = db.get("jobs", job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


def _pack(job: dict) -> dict:
    """The domain sheet this job runs on (falls back to the domain's base sheet
    when the job's exact area has no dedicated sheet yet)."""
    return load_pack(job.get("vertical") or vertical()["meta"]["vertical"],
                     job.get("area_code", ""))


def _norm(q: str) -> str:
    """Dedup key for questions: case/whitespace/punctuation-insensitive."""
    return " ".join(q.lower().split()).strip(" ?.!,;:")


def _learned(vertical_name: str, area_code: str) -> list[dict]:
    rows = [r for r in db.where("learned_questions", vertical=vertical_name, area_code=area_code)
            if r.get("status", "active") == "active"]
    rows.sort(key=lambda r: (-r.get("times_seen", 1), r.get("created_at", "")))
    return [{"question": r["question"], "why_it_matters": r.get("why_it_matters", ""),
             "times_seen": r.get("times_seen", 1)} for r in rows]


def _latest_call(job_id: str, company_id: str) -> dict | None:
    rows = db.where("calls", job_id=job_id, company_id=company_id)
    return sorted(rows, key=lambda c: c.get("started_at", ""))[-1] if rows else None


def _latest_call_field(job_id: str, company_id: str, field: str) -> str:
    call = _latest_call(job_id, company_id)
    return (call or {}).get(field, "")
