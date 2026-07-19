"""FastAPI app.

Two route families:
  /api/...          — the product API (Lovable dashboard talks to this)
  /agent-tools/...  — webhook tools the ElevenLabs agents call MID-CALL.

Honesty is architecture here: the negotiator agent has no way to state a
competing bid except get_competing_quotes, which reads the real quote DB.
It structurally cannot invent leverage.
"""
from fastapi import Depends, FastAPI, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import auth, db
from .benchmarks import counterparty_pricing, evaluate_red_flags, market_range
from .config import UPLOADS_DIR, personas, vertical
from .models import Company, Job, LearnedIn, LoginIn, OutcomeIn, QuoteIn, RegisterIn
from .packs import list_packs, load_pack
from .report import build_report

app = FastAPI(title="QuoteWise")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# --------------------------------------------------------------------------
# Auth — each user sees ONLY their own profile and jobs
# --------------------------------------------------------------------------
@app.post("/api/auth/register")
def register(body: RegisterIn):
    user = auth.create_user(body.email, body.password, body.name)
    return {"token": auth.issue_token(user), "user": auth.public(user)}


@app.post("/api/auth/login")
def login(body: LoginIn):
    user = auth.verify_user(body.email, body.password)
    if not user:
        raise HTTPException(401, "wrong email or password")
    return {"token": auth.issue_token(user), "user": auth.public(user)}


@app.post("/api/auth/logout")
def logout(authorization: str = Header("")):
    auth.revoke_token(authorization.removeprefix("Bearer ").strip())
    return {"logged_out": True}


@app.get("/api/me")
def me(user: dict = Depends(auth.current_user)):
    """The user's profile with their own jobs — and nobody else's."""
    return {"user": auth.public(user), "jobs": _user_jobs(user)}


# --------------------------------------------------------------------------
# Product API
# --------------------------------------------------------------------------
class JobCreate(BaseModel):
    vertical: str = ""              # defaults to the process VERTICAL pack
    area_code: str = ""             # defaults to the chosen sheet's own area


@app.post("/api/jobs")
def create_job(body: JobCreate | None = None, user: dict = Depends(auth.current_user)):
    vname = (body.vertical if body and body.vertical else vertical()["meta"]["vertical"])
    try:
        pack = load_pack(vname, body.area_code if body else "")
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    area = (body.area_code if body and body.area_code else pack["meta"].get("area_code", ""))
    job = Job(id=db.new_id("job"), vertical=vname, area_code=area, user_id=user["id"])
    db.put("jobs", job.id, job.model_dump())
    return job


@app.get("/api/jobs")
def list_jobs(user: dict = Depends(auth.current_user)):
    return _user_jobs(user)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, user: dict = Depends(auth.current_user)):
    return _owned_job(job_id, user)


class SpecBody(BaseModel):
    spec: dict


@app.put("/api/jobs/{job_id}/spec")
def put_spec(job_id: str, body: SpecBody, user: dict = Depends(auth.current_user)):
    """The web intake form's door — same rules as the voice interview:
    any spec change resets user confirmation."""
    job = _owned_job(job_id, user)
    job["spec"] = {**job["spec"], **body.spec}
    if "form" not in job["spec_source"]:
        job["spec_source"] = (job["spec_source"] + "+form").lstrip("+")
    job["confirmed"] = False
    db.put("jobs", job_id, job)
    missing = [f for f in _pack(job)["spec_schema"]["required"] if not job["spec"].get(f)]
    return {"job": job, "missing_required_fields": missing}


@app.post("/api/jobs/{job_id}/documents")
async def upload_document(job_id: str, file: UploadFile, user: dict = Depends(auth.current_user)):
    """Extra intake door: PDFs (other quotes, system/equipment specs), photos,
    text files. Extracted data is COMBINED with the call's data into the same
    job spec used for the calls — interview answers win, documents fill gaps,
    quotes found in documents become negotiation leverage."""
    from datetime import datetime, timezone
    from .docparse import parse_document  # lazy: needs OPENAI_API_KEY
    job = _owned_job(job_id, user)
    content = await file.read()
    extracted = parse_document(file.filename, content, _pack(job), job["spec"])

    doc_id = db.new_id("doc")
    folder = UPLOADS_DIR / job_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{doc_id}_{file.filename}").write_bytes(content)

    filled, updates = _merge_document(job["spec"], extracted)
    doc = {"id": doc_id, "filename": file.filename,
           "uploaded_at": datetime.now(timezone.utc).isoformat(),
           "extracted_fields": filled,
           "updates": updates,   # [{field, from, to}] — the doc corrected these
           "has_quote": bool(extracted.get("existing_quote")),
           "insights": extracted.get("insights", [])}
    job.setdefault("documents", []).append(doc)
    if "document" not in job["spec_source"]:
        job["spec_source"] = (job["spec_source"] + "+document").lstrip("+")
    if filled or updates:
        job["confirmed"] = False  # spec changed -> the user must re-confirm
    db.put("jobs", job_id, job)
    return {"job": job, "document": doc, "extracted": extracted}


@app.get("/api/jobs/{job_id}/documents")
def list_documents(job_id: str, user: dict = Depends(auth.current_user)):
    return _owned_job(job_id, user).get("documents", [])


@app.post("/api/jobs/{job_id}/voice-session")
def voice_session(job_id: str, user: dict = Depends(auth.current_user)):
    """Signed ElevenLabs session so the BROWSER can talk to the Estimator:
    the frontend starts a WebRTC/WS conversation with the user's mic, passing
    job_id as a dynamic variable — same interview, same spec, no CLI needed."""
    import json as _json
    import httpx
    from .config import ELEVENLABS_API_KEY, registry_path
    job = _owned_job(job_id, user)
    if not ELEVENLABS_API_KEY:
        raise HTTPException(503, "ELEVENLABS_API_KEY missing — voice is disabled.")
    reg = _json.loads(registry_path().read_text()) if registry_path().exists() else {}
    agent_id = reg.get("agents", {}).get("estimator", "")
    if not agent_id:
        raise HTTPException(503, "Estimator agent not provisioned — run `python -m agents.provision`.")
    r = httpx.get("https://api.elevenlabs.io/v1/convai/conversation/get-signed-url",
                  params={"agent_id": agent_id},
                  headers={"xi-api-key": ELEVENLABS_API_KEY}, timeout=20)
    if r.status_code >= 300:
        raise HTTPException(502, f"ElevenLabs refused the signed URL: {r.status_code} {r.text[:200]}")
    return {"signed_url": r.json()["signed_url"], "agent_id": agent_id,
            "dynamic_variables": {"job_id": job["id"]}}


@app.post("/api/jobs/{job_id}/confirm")
def confirm_spec(job_id: str, user: dict = Depends(auth.current_user)):
    job = _owned_job(job_id, user)
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
def generate_vertical(body: GenerateIn, user: dict = Depends(auth.current_user)):
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
def market(job_id: str, city: str, state: str, user: dict = Depends(auth.current_user)):
    """Real-world call-list discovery via Tavily (simulated personas stay the callable demo market)."""
    _owned_job(job_id, user)
    from .discovery import discover
    return {"discovered": discover(city, state), "note": "Demo calls run against the simulated personas."}


@app.get("/api/jobs/{job_id}/companies")
def companies(job_id: str, user: dict = Depends(auth.current_user)):
    _owned_job(job_id, user)
    return db.where("companies", job_id=job_id)


@app.get("/api/jobs/{job_id}/quotes")
def quotes(job_id: str, user: dict = Depends(auth.current_user)):
    _owned_job(job_id, user)
    return db.where("quotes", job_id=job_id)


@app.get("/api/jobs/{job_id}/calls")
def calls(job_id: str, user: dict = Depends(auth.current_user)):
    _owned_job(job_id, user)
    return db.where("calls", job_id=job_id)


@app.get("/api/jobs/{job_id}/report")
def report(job_id: str, user: dict = Depends(auth.current_user)):
    _owned_job(job_id, user)
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
    """Called by the Estimator at the end of the voice interview. Empty values
    are dropped so a partial save can never wipe fields already on file
    (False/0 are kept — they are real answers)."""
    job = _job(body.job_id)
    incoming = {k: v for k, v in body.spec.items() if v not in (None, "", [], {})}
    job["spec"] = {**job["spec"], **incoming}
    job["spec_source"] = ("interview+" + job["spec_source"]).rstrip("+") if job["spec_source"] else "interview"
    job["confirmed"] = False  # any spec change requires re-confirmation
    db.put("jobs", body.job_id, job)
    missing = [f for f in _pack(job)["spec_schema"]["required"] if not job["spec"].get(f)]
    return {"saved": True, "missing_required_fields": missing}


@app.post("/agent-tools/get_intake_form")
def t_get_intake_form(ref: JobRef):
    """The Estimator's FIRST call: the question list for this job's domain+area
    PLUS everything already on file (web form, documents, a previous call) —
    so the interview only asks for what's actually missing."""
    job = _job(ref.job_id)
    pack = _pack(job)
    spec = job.get("spec", {})
    on_file = {k: v for k, v in spec.items() if v not in (None, "", [], {})}
    missing = [f for f in pack["spec_schema"]["required"] if not spec.get(f)]
    return {"base_questions": pack["estimator_questions"],
            "learned_questions": _learned(job["vertical"], job.get("area_code", "")),
            "already_on_file": on_file,
            "missing_required_fields": missing,
            "note": "Ask ONLY about information NOT in already_on_file — never re-ask "
                    "what the customer already provided; acknowledge it in one short "
                    "sentence at most. Learned questions come from previous calls in "
                    "this service area — ask them too unless already covered."}


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
    doc_quotes = list(spec.get("existing_quotes") or [])
    if spec.get("existing_quote"):  # legacy single-quote key
        doc_quotes.append(spec["existing_quote"])
    for eq in doc_quotes:  # from document intake: leverage the user already had
        if eq.get("total"):
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


def _owned_job(job_id: str, user: dict) -> dict:
    """404 (not 403) for someone else's job: its existence is none of your business."""
    job = _job(job_id)
    if job.get("user_id") != user["id"]:
        raise HTTPException(404, "job not found")
    return job


def _user_jobs(user: dict) -> list[dict]:
    jobs = [j for j in db.where("jobs") if j.get("user_id") == user["id"]]
    return sorted(jobs, key=lambda j: j.get("created_at", ""), reverse=True)


def _merge_document(spec: dict, extracted: dict) -> tuple[list[str], list[dict]]:
    """Combine document data into the intake spec. A document doesn't just fill
    gaps: it can UPDATE fields already on file (the parser only emits a different
    value when the document is more authoritative). Every change is tracked as a
    diff for the frontend, and re-confirmation is forced upstream. Quotes
    accumulate as leverage, insights append to notes.
    Returns (filled_field_names, updates[{field, from, to}])."""
    filled, updates = [], []

    def _set(container: dict, key: str, new, label: str):
        cur = container.get(key)
        if not cur:
            container[key] = new
            filled.append(label)
        elif cur != new:
            updates.append({"field": label, "from": cur, "to": new})
            container[key] = new

    for k, v in extracted.items():
        if k in ("existing_quote", "existing_quotes", "insights", "notes", "vertical") \
                or v in (None, "", [], {}):
            continue
        if isinstance(v, dict) and isinstance(spec.get(k), dict):
            for sk, sv in v.items():
                if sv not in (None, "", [], {}):
                    _set(spec[k], sk, sv, f"{k}.{sk}")
        else:
            _set(spec, k, v, k)

    quotes = extracted.get("existing_quotes") or ([extracted["existing_quote"]]
                                                  if extracted.get("existing_quote") else [])
    for eq in quotes:
        if eq.get("total"):
            spec.setdefault("existing_quotes", []).append(eq)
            filled.append("existing_quotes")

    insights = [s for s in extracted.get("insights", []) if s]
    if insights:
        base = spec.get("notes") or ""
        spec["notes"] = (base + ("\n" if base else "")
                         + "\n".join(f"[doc] {s}" for s in insights))
        filled.append("notes")
    return filled, updates


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
