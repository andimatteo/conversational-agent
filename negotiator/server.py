"""FastAPI app.

Two route families:
  /api/...          — the product API (Lovable dashboard talks to this)
  /agent-tools/...  — webhook tools the ElevenLabs agents call MID-CALL.

Honesty is architecture here: the negotiator agent has no way to state a
competing bid except get_competing_quotes, which reads the real quote DB.
It structurally cannot invent leverage.
"""
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import auth, db
from .benchmarks import counterparty_pricing, evaluate_red_flags, market_range
from . import config
from .config import RECORDINGS_DIR, UPLOADS_DIR, personas, vertical
from .models import Company, Job, LearnedIn, LoginIn, OutcomeIn, QuoteIn, RegisterIn
from .packs import list_packs, load_pack
from .report import build_report
from market_discovery.router import get_discovery_service, router as call_list_router
from market_discovery.service import DiscoveryService

app = FastAPI(title="QuoteWise")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(call_list_router)


@app.middleware("http")
async def protect_agent_tools(request: Request, call_next):
    """Agent mutation/read tools are machine-to-machine endpoints, not a
    public second API. The provisioner attaches this header to every tool."""
    if request.url.path.startswith("/agent-tools/"):
        import hmac
        expected = config.AGENT_TOOL_SECRET
        supplied = request.headers.get("X-QuoteWise-Tool-Key", "")
        if not expected:
            return JSONResponse(status_code=503,
                                content={"detail": "AGENT_TOOL_SECRET is not configured"})
        if not hmac.compare_digest(supplied, expected):
            return JSONResponse(status_code=401, content={"detail": "invalid agent tool credentials"})
    return await call_next(request)


def _masked_phone(value: str) -> str:
    return f"•••{value[-4:]}" if len(value) >= 4 else ""


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


@app.get("/api/runtime-config")
def runtime_config(user: dict = Depends(auth.current_user)):
    """Non-secret, backend-authoritative runtime mode for the global UI banner."""
    return {
        "debug_mode": config.DEBUG_CALLS,
        "debug_behavior": "transcript_only" if config.DEBUG_CALLS else "voice_and_telephony",
        "debug_notice": (
            "Real Google vendor identities; no phone call, conversational session, or audio. "
            "Transcripts and quotes are generated and explicitly labelled synthetic. "
            "An explicitly prepared role-play job is a narrow exception: after a separate "
            "two-call confirmation, its preselected vendor identity routes only to the "
            "configured human in quote batch one and once more after every quote barrier."
            if config.DEBUG_CALLS else "Real outbound voice calls are enabled."
        ),
        "demo_phone_configured": bool(config.DEMO_PHONE_NUMBER),
        "demo_phone_masked": _masked_phone(config.DEMO_PHONE_NUMBER),
        "twilio_number_configured": bool(config.ELEVENLABS_PHONE_NUMBER_ID),
        "live_vendor_calls_enabled": config.LIVE_VENDOR_CALLS_ENABLED,
        "demo_intake_pdf_url": "/api/demo/intake-pdf",
        "call_list_ui_enabled": False,
        "review_launch_endpoint": "/api/jobs/{job_id}/launch",
        "google_places_live_at_launch": True,
        "google_places_configured": bool(config.GOOGLE_PLACES_API_KEY),
    }


@app.get("/api/demo/intake-pdf")
def demo_intake_pdf(user: dict = Depends(auth.current_user)):
    """Authenticated, reproducible intake document for the live run-of-show."""
    path = config.ROOT / "assets" / "demo" / "water_heater_intake.pdf"
    if not path.is_file():
        raise HTTPException(404, "Demo intake PDF is not available.")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename="QuoteWise-water-heater-intake.pdf",
    )


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
    from .spec_validation import sanitize_extracted
    clean, errors = sanitize_extracted(body.spec, _pack(job))
    if errors:
        raise HTTPException(422, {"message": "Spec contains invalid fields", "errors": errors})
    job["spec"] = {**job["spec"], **clean}
    if "form" not in job["spec_source"]:
        job["spec_source"] = (job["spec_source"] + "+form").lstrip("+")
    job["confirmed"] = False
    db.put("jobs", job_id, job)
    missing = _missing_required(job["spec"], _pack(job))
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
    from .spec_validation import sanitize_extracted
    # Parser metadata is kept outside the domain spec; every candidate spec
    # field is schema-checked before it can influence a confirmed job.
    metadata = {"insights": extracted.get("insights", [])}
    clean, validation_errors = sanitize_extracted(
        {k: v for k, v in extracted.items() if k not in ("insights", "vertical")}, _pack(job))
    extracted = {**clean, **{k: v for k, v in metadata.items() if v not in (None, "", [], {})}}

    doc_id = db.new_id("doc")
    # Preserve provenance inside every extracted quote. It becomes usable as
    # leverage only after the user re-confirms the merged specification.
    document_quotes = extracted.get("existing_quotes") or (
        [extracted["existing_quote"]] if extracted.get("existing_quote") else [])
    for quote in document_quotes:
        quote["_document_id"] = doc_id
        quote["_document_filename"] = file.filename
    folder = UPLOADS_DIR / job_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{doc_id}_{file.filename}").write_bytes(content)

    filled, updates = _merge_document(job["spec"], extracted)
    doc = {"id": doc_id, "filename": file.filename,
           "uploaded_at": datetime.now(timezone.utc).isoformat(),
           "extracted_fields": filled,
           "updates": updates,   # [{field, from, to}] — the doc corrected these
           "has_quote": bool(extracted.get("existing_quote")),
           "insights": extracted.get("insights", []),
           "validation_errors": validation_errors}
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
    if reg.get("meta", {}).get("vertical") != job.get("vertical"):
        raise HTTPException(503, "Estimator is provisioned for a different domain — set VERTICAL and re-provision.")
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


# --------------------------------------------------------------------------
# The call queue: seed the simulated market, start calls, watch it resolve
# --------------------------------------------------------------------------
@app.post("/api/jobs/{job_id}/companies/simulated")
def seed_simulated_market(job_id: str, user: dict = Depends(auth.current_user)):
    """Create this job's callable market: one company per counterparty persona
    of the job's domain (idempotent). Demo calls run agent-to-agent against
    these three negotiation styles."""
    import json as _json
    from .config import registry_path
    job = _owned_job(job_id, user)
    existing = {c.get("persona") for c in db.where("companies", job_id=job_id)}
    agents = (_json.loads(registry_path().read_text()).get("agents", {})
              if registry_path().exists() else {})
    created = []
    for p in personas(job.get("vertical")):
        if p["id"] in existing:
            continue
        co = Company(id=db.new_id("co"), name=p["company_name"], persona=p["id"],
                     source="simulated", agent_id=agents.get(f"counterparty:{p['id']}", ""))
        db.put("companies", co.id, co.model_dump(), job_id=job_id)
        created.append({"id": co.id, "name": co.name, "style": p["style"]})
    return {"created": created, "companies": db.where("companies", job_id=job_id)}


class FromCallListIn(BaseModel):
    count: int = 0                  # 0 = every callable Google Places vendor
    companies: list[dict] = []      # or explicit picks: [{name, phone}]


@app.post("/api/jobs/{job_id}/companies/from-call-list")
def companies_from_call_list(job_id: str, body: FromCallListIn,
                             user: dict = Depends(auth.current_user)):
    """Promote every real Google Places lead into the scheduler.

    With global debug enabled their identity stays real while only the
    transcript/quote is simulated.  With debug disabled the same record is the
    actual Twilio destination.  ``count`` remains only for old clients.
    """
    job = _owned_job(job_id, user)
    discovered = [item for item in job.get("call_list", {}).get("items", [])
                  if "google_places" in item.get("sources", []) and item.get("phone")]
    if body.companies:
        # Explicit picks are references to server-discovered leads, never an
        # arbitrary client-supplied dial list.
        by_phone = {item["phone"]: item for item in discovered}
        picks = []
        for requested in body.companies:
            actual = by_phone.get(requested.get("phone", ""))
            if not actual or actual.get("name") != requested.get("name"):
                raise HTTPException(422, "Every selected company must exactly match a saved Google Places lead.")
            picks.append(actual)
    else:
        picks = discovered
    if body.count > 0:
        picks = picks[:body.count]
    if not picks:
        raise HTTPException(404, "No callable Google Places vendors on this job — scan the market first.")
    if not body.companies and body.count == 0:
        from .callrunner import sync_google_companies
        created = sync_google_companies(job_id)
        return {"created": created, "companies": created,
                "debug_mode": config.DEBUG_CALLS,
                "note": "All real Google Places vendors are scheduled; debug mode generates labelled transcripts only."
                if config.DEBUG_CALLS else "All real Google Places vendors are available for outbound calling."}

    ps = personas(job.get("vertical"))
    existing = {c.get("phone"): c for c in db.where("companies", job_id=job_id)}
    created = []
    for i, item in enumerate(p for p in picks if p.get("name") and p.get("phone")):
        p = ps[i % len(ps)]
        old = existing.get(item["phone"], {})
        co = Company(id=old.get("id") or db.new_id("co"), name=item["name"],
                     phone=item["phone"], source="google_places", persona=p["id"],
                     rating=item.get("rating"), review_count=item.get("review_count"),
                     address=item.get("address", ""),
                     latitude=item.get("latitude"), longitude=item.get("longitude"),
                     url=item.get("url", ""), categories=item.get("categories", []),
                     discovery_sources=item.get("sources", ["google_places"]),
                     external_ids=item.get("source_ids", {}))
        db.put("companies", co.id, co.model_dump(), job_id=job_id)
        created.append({"id": co.id, "name": co.name, "style": p["style"],
                        "source": "google_places"})
    return {"created": created, "companies": db.where("companies", job_id=job_id),
            "debug_mode": config.DEBUG_CALLS}


@app.get("/api/jobs/{job_id}/call-queue")
def call_queue(job_id: str, user: dict = Depends(auth.current_user)):
    """Live queue: per-company status (to_call/queued/calling/quote/callback/
    decline/hangup) + totals. Poll this while calls run."""
    _owned_job(job_id, user)
    from . import callrunner
    return callrunner.queue_state(job_id)


class StartCallsIn(BaseModel):
    phase: str = "quote"            # quote | negotiate
    company_ids: list[str] = []
    parallel: bool | None = None     # deprecated; server always uses sqrt(n)
    retry_completed: bool = False
    recommended_only: bool = False
    idempotency_key: str = Field(default="", max_length=128)  # retry-safe request id
    # Prepared hybrid demos place two calls to the allow-listed human. A
    # deliberate per-run acknowledgement prevents stale clients from starting
    # that sequence while believing global debug mode is fully transcript-only.
    authorize_demo_calls: bool = False


@app.post("/api/jobs/{job_id}/calls/start")
def start_calls(job_id: str, body: StartCallsIn, user: dict = Depends(auth.current_user)):
    """Kick off the calls server-side (background); the queue endpoint shows
    progress. Spec must be confirmed first — the backend enforces it. On an
    explicitly prepared role-play job, the preselected Google lead is routed
    server-side to the allow-listed human in quote batch one and recalled only
    after every quote barrier. `authorize_demo_calls=true` is required; no
    discovered Google phone is dialled."""
    job = _owned_job(job_id, user)
    if body.phase not in ("quote", "negotiate"):
        raise HTTPException(422, "phase must be 'quote' or 'negotiate'")
    if not job.get("confirmed"):
        raise HTTPException(409, "Spec not confirmed — confirm it before any call.")
    from . import callrunner
    try:
        return callrunner.start_calls(job_id, body.phase, body.company_ids or None, body.parallel,
                                      retry_completed=body.retry_completed,
                                      recommended_only=body.recommended_only,
                                      idempotency_key=body.idempotency_key or None,
                                      authorize_demo_calls=body.authorize_demo_calls)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))


class DemoCallIn(BaseModel):
    company_id: str
    phase: str = "negotiate"        # quote | negotiate


@app.post("/api/jobs/{job_id}/calls/demo")
def demo_call(job_id: str, body: DemoCallIn, user: dict = Depends(auth.current_user)):
    """Call the single server-configured demo phone through the imported
    Twilio number while preserving the selected Google vendor record.

    For a prepared role-play job the initial live quote belongs to the bulk
    run; this endpoint is then negotiation-only. Normal jobs retain the legacy
    explicit quote/negotiate behavior.
    """
    job = _owned_job(job_id, user)
    if body.phase not in ("quote", "negotiate"):
        raise HTTPException(422, "phase must be 'quote' or 'negotiate'")
    if not job.get("confirmed"):
        raise HTTPException(409, "Spec not confirmed — confirm it before any call.")
    from . import callrunner
    try:
        return callrunner.start_demo_call(job_id, body.company_id, body.phase)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"ElevenLabs/Twilio demo call failed: {str(exc)[:300]}")


@app.get("/api/jobs/{job_id}/follow-ups")
def follow_ups(job_id: str, user: dict = Depends(auth.current_user)):
    job = _owned_job(job_id, user)
    return {"knowledge_version": job.get("knowledge_version", 0),
            "recommendations": job.get("follow_up_plan", [])}


class LaunchJobIn(BaseModel):
    """One deliberate review action: live discovery plus the two-call demo."""

    model_config = ConfigDict(extra="forbid")
    authorize_demo_calls: bool = False
    idempotency_key: str = Field(min_length=1, max_length=128)


@app.post("/api/jobs/{job_id}/launch")
def launch_job(job_id: str, body: LaunchJobIn,
               user: dict = Depends(auth.current_user),
               service: DiscoveryService = Depends(get_discovery_service)):
    """Confirm a prepared demo, call Google Places live, then start batches.

    Market discovery is real and happens only after the user reviews the spec.
    Telephony remains separate: N-1 Places identities get synthetic transcript
    calls, while the selected identity is routed only to the allow-listed human
    in quote batch one and the automatic final negotiation batch.
    """
    job = _owned_job(job_id, user)
    demo = job.get("demo_mode") if isinstance(job.get("demo_mode"), dict) else {}
    if job.get("archived") or not (demo.get("active") and demo.get("roleplay")):
        raise HTTPException(409, "This launch flow is available only for an active prepared demo.")
    if not body.authorize_demo_calls:
        raise HTTPException(
            409,
            "Review requires explicit authorization for exactly two calls to the allow-listed human.",
        )
    if not job.get("documents") or "document" not in job.get("spec_source", ""):
        raise HTTPException(409, "Upload the demo document before review and launch.")
    from .spec_validation import validate_spec
    errors = validate_spec(job.get("spec", {}), _pack(job))
    if errors:
        raise HTTPException(422, {"message": "Spec is not valid and cannot be launched",
                                  "errors": errors})

    launch = job.get("launch") if isinstance(job.get("launch"), dict) else {}
    previous_key = launch.get("idempotency_key", "")
    if previous_key and previous_key != body.idempotency_key:
        raise HTTPException(
            409,
            "This job already has a launch attempt. Reuse its idempotency key; never create a second campaign.",
        )
    if not previous_key:
        claimed = db.compare_and_set_json(
            "jobs", job_id, "launch", job.get("launch"),
            {"launch": {
                "idempotency_key": body.idempotency_key,
                "status": "discovering_google_places",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        if claimed is None:
            raise HTTPException(409, "Another launch request is already preparing this job.")
        job = claimed
        launch = job["launch"]

    discovery = job.get("call_list", {})
    fresh_discovery_ready = bool(
        discovery.get("discovery_mode") == "live_google_places_at_launch"
        and discovery.get("saved") and discovery.get("items")
    )
    if not fresh_discovery_ready:
        settings = demo.get("discovery", {})
        query = str(settings.get("query") or _pack(job)["meta"]["counterparty_noun"])
        state = str(settings.get("state") or "North Carolina")
        target = int(settings.get("target") or 25)
        try:
            discovery = service.discover_google_places(query, state, target)
        except ValueError as exc:
            failed = db.get("jobs", job_id) or job
            failed["launch"] = {**launch, "status": "discovery_failed", "error": str(exc)[:300]}
            failed["demo_mode"] = {**demo, "status": "google_places_failed"}
            db.put("jobs", job_id, failed)
            raise HTTPException(503, str(exc))
        except Exception as exc:
            failed = db.get("jobs", job_id) or job
            failed["launch"] = {**launch, "status": "discovery_failed", "error": str(exc)[:300]}
            failed["demo_mode"] = {**demo, "status": "google_places_failed"}
            db.put("jobs", job_id, failed)
            raise HTTPException(502, f"Live Google Places discovery failed: {str(exc)[:250]}")
        if not discovery.get("saved") or not discovery.get("items"):
            raise HTTPException(404, "Live Google Places discovery returned no callable businesses.")
        job = db.get("jobs", job_id) or job
        job["call_list"] = discovery
        job["launch"] = {**launch, "status": "promoting_google_places",
                         "google_places_generated_at": discovery.get("generated_at", "")}
        db.put("jobs", job_id, job)

    from . import callrunner
    from .demo_reset import _select_live_vendor
    companies = callrunner.sync_google_companies(job_id)
    demo = (db.get("jobs", job_id) or job).get("demo_mode", demo)
    try:
        selected = _select_live_vendor(companies, demo.get("selection_query", ""))
    except LookupError as exc:
        raise HTTPException(404, str(exc))

    job = db.get("jobs", job_id) or job
    job["confirmed"] = True
    job["demo_mode"] = {
        **demo,
        "status": "google_places_complete_starting_calls",
        "workflow_stage": "calls",
        "live_company_id": selected["id"],
        "live_company_name": selected["name"],
        "live_company_google_place_id": selected.get("external_ids", {}).get("google_places", ""),
        "discovery": {
            **demo.get("discovery", {}),
            "status": "completed",
            "generated_at": discovery.get("generated_at", ""),
            "result_count": len(companies),
            "live_api": True,
        },
    }
    job["launch"] = {**job.get("launch", launch), "status": "starting_calls",
                     "selected_company_id": selected["id"]}
    db.put("jobs", job_id, job)

    try:
        run = callrunner.start_calls(
            job_id, "quote", idempotency_key=body.idempotency_key,
            authorize_demo_calls=True,
        )
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))

    job = db.get("jobs", job_id) or job
    job["launch"] = {**job.get("launch", launch), "status": "launched",
                     "run_id": run.get("run_id", "")}
    job["demo_mode"] = {**job["demo_mode"], "status": "calls_running",
                        "demo_calls_authorized": True}
    db.put("jobs", job_id, job)
    return {
        "launched": True,
        "redirect": f"/job/{job_id}/calls",
        "discovery": {
            "provider": "google_places",
            "live_api": True,
            "generated_at": discovery.get("generated_at", ""),
            "raw_results": discovery.get("raw_results", 0),
            "callable_vendors": len(companies),
        },
        "live_company": {"id": selected["id"], "name": selected["name"]},
        "run": run,
    }


@app.post("/api/jobs/{job_id}/confirm")
def confirm_spec(job_id: str, user: dict = Depends(auth.current_user)):
    job = _owned_job(job_id, user)
    demo = job.get("demo_mode") if isinstance(job.get("demo_mode"), dict) else {}
    if demo.get("active") and demo.get("roleplay"):
        raise HTTPException(
            409,
            f"Prepared demos use POST /api/jobs/{job_id}/launch so review triggers fresh "
            "Google Places discovery and the batch campaign atomically.",
        )
    if job.get("spec", {}).get("vertical", job["vertical"]) != job["vertical"]:
        raise HTTPException(422, "Spec vertical does not match the job vertical")
    from .spec_validation import validate_spec
    errors = validate_spec(job["spec"], _pack(job))
    if errors:
        raise HTTPException(422, {"message": "Spec is not valid and cannot be confirmed",
                                  "errors": errors})
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
def market(job_id: str, user: dict = Depends(auth.current_user)):
    """Deprecated: call lists must use Google Places + Yelp + OSM."""
    _owned_job(job_id, user)
    raise HTTPException(410, f"Use POST /api/jobs/{job_id}/call-list/discover")


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
    out = []
    for call in db.where("calls", job_id=job_id):
        row = dict(call)
        row["has_audio"] = bool(row.get("audio_path"))
        row["audio_url"] = (f"/api/jobs/{job_id}/calls/{row['id']}/audio"
                            if row["has_audio"] else "")
        row.pop("audio_path", None)
        out.append(row)
    return out


@app.get("/api/jobs/{job_id}/calls/{call_id}/audio")
def call_audio(job_id: str, call_id: str, user: dict = Depends(auth.current_user)):
    """Authenticated recording playback for Lovable. Debug calls return 404
    because transcript-only mode intentionally creates no audio artifact."""
    _owned_job(job_id, user)
    call = db.get("calls", call_id)
    if not call or call.get("job_id") != job_id or not call.get("audio_path"):
        raise HTTPException(404, "No recording exists for this call.")
    from pathlib import Path
    path = Path(call["audio_path"]).resolve()
    root = RECORDINGS_DIR.resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "Recording not found.")
    return FileResponse(path, media_type="audio/mpeg", filename=f"{call_id}.mp3")


@app.get("/api/jobs/{job_id}/report")
def report(job_id: str, user: dict = Depends(auth.current_user)):
    _owned_job(job_id, user)
    return build_report(job_id)


# --------------------------------------------------------------------------
# Agent tools (ElevenLabs webhook tools) — called mid-call
# --------------------------------------------------------------------------
class JobRef(BaseModel):
    job_id: str
    call_id: str = ""


class CompanyRef(BaseModel):
    job_id: str
    company_id: str
    call_id: str = ""


class SpecIn(BaseModel):
    job_id: str
    spec: dict


@app.post("/agent-tools/get_job_spec")
def t_get_job_spec(ref: JobRef):
    job = _job(ref.job_id)
    if not job["confirmed"]:
        raise HTTPException(409, "Job spec not confirmed by the user yet — no calls allowed.")
    if ref.call_id:
        call = _call(ref.call_id, ref.job_id)
        frozen = call.get("knowledge_snapshot", {}).get("spec")
        if frozen:
            return {"spec": frozen, "spec_hash": call.get("spec_hash", ""),
                    "knowledge_version": call.get("knowledge_version", 0)}
    return {"spec": job["spec"]}


@app.post("/agent-tools/get_call_context")
def t_get_call_context(ref: CompanyRef):
    """Atomic honesty gate: frozen spec, own history and only the competing
    facts that existed at this batch's start."""
    job = _job(ref.job_id)
    if not job.get("confirmed"):
        raise HTTPException(409, "Job spec not confirmed by the user yet — no calls allowed.")
    call = _resolve_call(ref.job_id, ref.company_id, ref.call_id)
    context = call.get("knowledge_snapshot")
    if not context:
        from .knowledge import context_for, create_snapshot
        context = context_for(create_snapshot(ref.job_id, job.get("knowledge_version", 0)),
                              ref.company_id)
        call["knowledge_snapshot"] = context
        call["knowledge_version"] = context.get("knowledge_version", 0)
        call["spec_hash"] = context.get("spec_hash", "")
        db.put("calls", call["id"], call, job_id=ref.job_id, company_id=ref.company_id)
    return {**context, "call_id": call["id"], "company_id": ref.company_id}


@app.post("/agent-tools/get_company_history")
def t_get_company_history(ref: CompanyRef):
    """The counterparty's grounded memory on a recall. It exposes only that
    company's own recorded offers, never competitors."""
    context = t_get_call_context(ref)
    return {"call_id": context["call_id"],
            "knowledge_version": context.get("knowledge_version", 0),
            "own_quote_history": context.get("own_quote_history", []),
            "rule": "Acknowledge only these prior offers; if empty, say no prior quote is verified."}


@app.post("/agent-tools/save_job_spec")
def t_save_job_spec(body: SpecIn):
    """Called by the Estimator at the end of the voice interview. Empty values
    are dropped so a partial save can never wipe fields already on file
    (False/0 are kept — they are real answers)."""
    job = _job(body.job_id)
    incoming = {k: v for k, v in body.spec.items() if v not in (None, "", [], {})}
    declared_vertical = incoming.pop("vertical", job.get("vertical", ""))
    if declared_vertical != job.get("vertical"):
        raise HTTPException(422, "Estimator returned a spec for the wrong vertical")
    from .spec_validation import sanitize_extracted
    clean, errors = sanitize_extracted(incoming, _pack(job))
    if errors:
        raise HTTPException(422, {"message": "Estimator returned an invalid spec", "errors": errors})
    job["spec"] = {**job["spec"], **clean}
    job["spec_source"] = ("interview+" + job["spec_source"]).rstrip("+") if job["spec_source"] else "interview"
    job["confirmed"] = False  # any spec change requires re-confirmation
    db.put("jobs", body.job_id, job)
    missing = _missing_required(job["spec"], _pack(job))
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
    missing = _missing_required(spec, pack)
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
    job = _job(body.job_id)
    if body.call_id:
        call = _call(body.call_id, body.job_id, body.company_id)
        if call.get("ended_at"):
            raise HTTPException(409, "This call is already terminal; late learning writes are rejected")
    elif body.company_id:
        if not any(company.get("id") == body.company_id
                   for company in db.where("companies", job_id=body.job_id)):
            raise HTTPException(422, "company_id does not belong to this job")
    from .learnings import persist_questions
    base = {_norm(q) for q in _pack(job)["estimator_questions"]}
    aggregate = {"logged": True, "added": [], "updated": [], "already_known": []}
    for question in body.questions:
        if _norm(question.question) in base:
            aggregate["already_known"].append(question.question)
            continue
        result = persist_questions(job, [question], source_call_id=body.call_id,
                                   company_id=body.company_id)
        for key in ("added", "updated", "already_known"):
            aggregate[key].extend(result.get(key, []))
    return aggregate


@app.post("/agent-tools/log_quote")
def t_log_quote(q: QuoteIn):
    job = _job(q.job_id)
    company = db.get("companies", q.company_id)
    if not company or company not in db.where("companies", job_id=q.job_id):
        raise HTTPException(422, "company_id does not belong to this job")
    call = _resolve_call(q.job_id, q.company_id, q.call_id, required=False)
    if q.call_id and not call:
        raise HTTPException(404, "call_id not found for this job+company")
    if call and call.get("ended_at"):
        raise HTTPException(409, "This call is already terminal; late quote writes are rejected")
    if call:
        expected_phase = "initial" if call.get("kind") == "quote" else "negotiated"
        if q.phase != expected_phase:
            raise HTTPException(422, f"phase={q.phase} is incompatible with a {call.get('kind')} call")
    if q.phase == "negotiated" and q.leverage_quote_ids and call:
        allowed = {row["quote_id"] for row in call.get("knowledge_snapshot", {}).get(
            "allowed_competitive_claims", [])}
        invalid = set(q.leverage_quote_ids) - allowed
        if invalid:
            raise HTTPException(422, f"Ungrounded leverage quote ids: {sorted(invalid)}")
    allowed_codes = set(_pack(job).get("fee_taxonomy", {})) | {"other"}
    invalid_codes = sorted({item.code for item in q.line_items if item.code not in allowed_codes})
    if invalid_codes:
        raise HTTPException(422, f"Unknown fee taxonomy codes: {invalid_codes}")
    data = q.model_dump()
    frozen_spec = (call or {}).get("knowledge_snapshot", {}).get("spec", job["spec"])
    data["red_flags"] = evaluate_red_flags(q, frozen_spec, _pack(job))
    itemized_total = round(sum(item.amount for item in q.line_items), 2)
    data["itemization_delta"] = round(q.total - itemized_total, 2)
    data["itemization_verified"] = abs(data["itemization_delta"]) <= 1.0
    if not data["itemization_verified"]:
        data.setdefault("validation_warnings", []).append(
            f"Line items sum to {itemized_total}, not the stated total {q.total}.")
    data["id"] = db.new_id("quote")
    data["call_id"] = (call or {}).get("id", q.call_id)
    data["conversation_id"] = (call or {}).get("conversation_id", "")
    data["batch_id"] = (call or {}).get("batch_id", "")
    data["knowledge_version"] = (call or {}).get("knowledge_version", 0)
    data["evidence_verified"] = False  # post-call finalizer checks transcript verbatim
    data["uncorrelated"] = call is None
    from datetime import datetime, timezone
    data["created_at"] = datetime.now(timezone.utc).isoformat()
    db.put("quotes", data["id"], data, job_id=q.job_id, company_id=q.company_id, phase=q.phase)
    return {"logged": True, "red_flags": data["red_flags"]}


@app.post("/agent-tools/log_call_outcome")
def t_log_outcome(o: OutcomeIn):
    call = _resolve_call(o.job_id, o.company_id, o.call_id, required=False)
    if call is None:
        raise HTTPException(404, "No active call record for this job+company.")
    if call.get("ended_at"):
        raise HTTPException(409, "This call is already terminal; duplicate outcome rejected")
    if o.outcome == "quote":
        has_quote = any(q.get("call_id") == call["id"] for q in
                        db.where("quotes", job_id=o.job_id, company_id=o.company_id))
        if not has_quote:
            raise HTTPException(409, "outcome=quote requires a structured quote on this call")
    if o.outcome == "callback" and not o.callback_time.strip():
        raise HTTPException(422, "callback_time is required for outcome=callback")
    if o.outcome == "decline" and not o.decline_reason.strip():
        raise HTTPException(422, "decline_reason is required for outcome=decline")
    call.update(o.model_dump(exclude={"job_id", "company_id", "call_id"}))
    db.put("calls", call["id"], call, job_id=o.job_id, company_id=o.company_id)
    return {"logged": True}


@app.post("/agent-tools/get_competing_quotes")
def t_competing_quotes(ref: CompanyRef):
    """Backward-compatible view over the frozen atomic call context."""
    call = _resolve_call(ref.job_id, ref.company_id, ref.call_id, required=False)
    if call and (ref.call_id or not call.get("ended_at")):
        context = call.get("knowledge_snapshot", {})
    else:  # offline/legacy inspection outside a running call
        from .knowledge import context_for, create_snapshot
        job = _job(ref.job_id)
        context = context_for(create_snapshot(ref.job_id, job.get("knowledge_version", 0)),
                              ref.company_id)
    return {"competing_quotes": context.get("competing_quotes", []),
            "knowledge_version": context.get("knowledge_version", 0),
            "rules": context.get("rules", "Cite only returned quotes.")}


@app.post("/agent-tools/get_benchmark")
def t_benchmark(ref: JobRef):
    job = _job(ref.job_id)
    if ref.call_id:
        call = _call(ref.call_id, ref.job_id)
        benchmark = call.get("knowledge_snapshot", {}).get("benchmark")
        if benchmark:
            return benchmark
    return market_range(job["spec"], _pack(job))


@app.post("/agent-tools/counterparty_pricing")
def t_counterparty_pricing(ref: CompanyRef):
    """The simulated company's private back office. Only counterparty agents have this tool."""
    co = db.get("companies", ref.company_id)
    if not co or not co.get("persona"):
        raise HTTPException(404, "Not a simulated company.")
    job = _job(ref.job_id)
    persona = next(p for p in personas(job.get("vertical")) if p["id"] == co["persona"])
    call = _resolve_call(ref.job_id, ref.company_id, ref.call_id, required=False)
    frozen_spec = (call or {}).get("knowledge_snapshot", {}).get("spec", job["spec"])
    return counterparty_pricing(persona, frozen_spec, _pack(job))


# --------------------------------------------------------------------------
def _job(job_id: str) -> dict:
    job = db.get("jobs", job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


def _call(call_id: str, job_id: str = "", company_id: str = "") -> dict:
    call = db.get("calls", call_id)
    if not call or (job_id and call.get("job_id") != job_id) \
            or (company_id and call.get("company_id") != company_id):
        raise HTTPException(404, "call not found")
    return call


def _resolve_call(job_id: str, company_id: str, call_id: str = "",
                  required: bool = True) -> dict | None:
    if call_id:
        try:
            return _call(call_id, job_id, company_id)
        except HTTPException:
            if required:
                raise
            return None
    rows = db.where("calls", job_id=job_id, company_id=company_id)
    active = [row for row in rows if not row.get("ended_at")]
    found = _latest_call(job_id, company_id) if not active else sorted(
        active, key=lambda c: c.get("created_at") or c.get("started_at", ""))[-1]
    if not found and required:
        raise HTTPException(404, "No call record for this job+company")
    return found


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


def _missing_required(spec: dict, pack: dict) -> list[str]:
    def missing(value):
        return value is None or (isinstance(value, str) and not value.strip()) \
            or (isinstance(value, (list, dict)) and not value)
    return [field for field in pack["spec_schema"]["required"]
            if field not in spec or missing(spec[field])]


def _learned(vertical_name: str, area_code: str) -> list[dict]:
    rows = [r for r in db.where("learned_questions", vertical=vertical_name, area_code=area_code)
            if r.get("status", "active") == "active"]
    rows.sort(key=lambda r: (-r.get("times_seen", 1), r.get("created_at", "")))
    return [{"question": r["question"], "why_it_matters": r.get("why_it_matters", ""),
             "times_seen": r.get("times_seen", 1)} for r in rows]


def _latest_call(job_id: str, company_id: str) -> dict | None:
    rows = db.where("calls", job_id=job_id, company_id=company_id)
    return sorted(rows, key=lambda c: c.get("created_at") or c.get("started_at", ""))[-1] if rows else None


def _latest_call_field(job_id: str, company_id: str, field: str) -> str:
    call = _latest_call(job_id, company_id)
    return (call or {}).get(field, "")
