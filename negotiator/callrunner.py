"""Batch call orchestration with frozen knowledge and a safe debug mode.

For ``n`` eligible vendors the scheduler uses ``ceil(sqrt(n))`` concurrent
calls per batch, waits for every call in that batch to become terminal, then
publishes the new knowledge snapshot before starting the next batch.
"""
from __future__ import annotations

import json
import math
import threading
import time
from datetime import datetime, timezone

import httpx

from . import config, db
from .benchmarks import evaluate_red_flags
from .knowledge import context_for, create_snapshot, follow_up_plan, latest_offers, spec_hash
from .models import Company, QuoteIn
from .packs import load_pack

API = "https://api.elevenlabs.io/v1/convai"
TERMINAL_BATCH_STATUSES = {"completed", "failed", "cancelled"}
TERMINAL_RECIPIENT_STATUSES = {"completed", "failed", "cancelled", "no_answer", "busy"}
TERMINAL_CALL_STATUSES = {"completed", "failed"}

# One bulk orchestration at a time per job.  Persistent run/batch records are
# the source of truth; these maps only make the single-process demo responsive.
_runs: dict[str, threading.Thread] = {}
_queued: dict[str, set[str]] = {}
_knowledge_lock = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def computed_batch_size(n: int) -> int:
    return max(1, math.ceil(math.sqrt(max(1, n))))


def _chunks(rows: list[dict], size: int) -> list[list[dict]]:
    return [rows[i:i + size] for i in range(0, len(rows), size)]


def _latest(rows: list[dict], field: str = "created_at") -> dict | None:
    return sorted(rows, key=lambda row: row.get(field, ""))[-1] if rows else None


def _demo_mode(job: dict | None) -> dict:
    mode = (job or {}).get("demo_mode")
    return mode if (
        isinstance(mode, dict)
        and mode.get("roleplay")
        and mode.get("active")
        and not (job or {}).get("archived")
    ) else {}


def _advance_knowledge(job_id: str) -> tuple[int, list[dict]]:
    """Publish a new job knowledge version atomically, then attach the plan
    only if no other worker advanced the version while it was computed."""
    with _knowledge_lock:
        job = db.increment_json_field("jobs", job_id, "knowledge_version")
        while True:
            version = int(job.get("knowledge_version", 0))
            plan = follow_up_plan(job_id, version)
            saved = db.compare_and_set_json(
                "jobs", job_id, "knowledge_version", version,
                {"follow_up_plan": plan},
            )
            if saved is not None:
                return version, plan
            job = db.get("jobs", job_id)


def sync_google_companies(job_id: str) -> list[dict]:
    """Idempotently promote *every* callable Google Places lead.

    The company name, phone and Google place id remain the real discovery
    record.  A persona label is only a deterministic debug/demo behaviour tag.
    """
    job = db.get("jobs", job_id) or {}
    items = [item for item in job.get("call_list", {}).get("items", [])
             if item.get("phone") and "google_places" in item.get("sources", [])]
    if not items:
        return []

    from .config import personas
    persona_rows = personas(job.get("vertical"))
    existing = db.where("companies", job_id=job_id)
    by_phone = {c.get("phone"): c for c in existing if c.get("phone")}
    by_place = {c.get("external_ids", {}).get("google_places"): c for c in existing
                if c.get("external_ids", {}).get("google_places")}

    out = []
    # A phone number is the actual dial target. Google may return the same
    # switchboard for duplicate Places records or multiple service-area
    # listings; calling it twice would both skew coverage and harass the
    # recipient. Keep the first (discovery is already relevance-sorted).
    emitted_phones: set[str] = set()
    emitted_company_ids: set[str] = set()
    for index, item in enumerate(items):
        if item["phone"] in emitted_phones:
            continue
        place_id = item.get("source_ids", {}).get("google_places", "")
        current = by_place.get(place_id) or by_phone.get(item["phone"])
        persona = persona_rows[index % len(persona_rows)] if persona_rows else {"id": ""}
        payload = Company(
            id=(current or {}).get("id") or db.new_id("co"),
            name=item["name"],
            phone=item["phone"],
            source="google_places",
            persona=(current or {}).get("persona") or persona.get("id", ""),
            agent_id=(current or {}).get("agent_id", ""),
            rating=item.get("rating"),
            review_count=item.get("review_count"),
            address=item.get("address") or item.get("city", ""),
            latitude=item.get("latitude"),
            longitude=item.get("longitude"),
            url=item.get("url", ""),
            categories=item.get("categories", []),
            discovery_sources=item.get("sources", []),
            external_ids=item.get("source_ids", {}),
            demo_roleplay=bool((current or {}).get("demo_roleplay", False)),
            demo_alias=(current or {}).get("demo_alias", ""),
        ).model_dump()
        db.put("companies", payload["id"], payload, job_id=job_id)
        by_phone[payload["phone"]] = payload
        if place_id:
            by_place[place_id] = payload
        emitted_phones.add(payload["phone"])
        if payload["id"] not in emitted_company_ids:
            emitted_company_ids.add(payload["id"])
            out.append(payload)
    return out


def queue_state(job_id: str) -> dict:
    """Realtime board payload: rows, risk-adjusted best, range and batch state."""
    job = db.get("jobs", job_id) or {}
    calls = db.where("calls", job_id=job_id)
    quotes = db.where("quotes", job_id=job_id)
    companies = db.where("companies", job_id=job_id)
    plans = {p["company_id"]: p for p in job.get("follow_up_plan", [])}
    from .recall_limits import for_company as recall_reservations_for_company
    rows = []
    for company in companies:
        company_calls = sorted((c for c in calls if c["company_id"] == company["id"]),
                               key=lambda c: c.get("created_at") or c.get("started_at", ""))
        latest_call = company_calls[-1] if company_calls else None
        company_quotes = sorted((q for q in quotes if q["company_id"] == company["id"]),
                                key=lambda q: q.get("created_at", ""))
        initial = next((q for q in reversed(company_quotes) if q.get("phase") == "initial"), None)
        negotiated = next((q for q in reversed(company_quotes) if q.get("phase") == "negotiated"), None)

        if latest_call and latest_call.get("status") in {"queued", "calling"}:
            status = latest_call["status"]
        elif latest_call and latest_call.get("started_at") and not latest_call.get("ended_at"):
            status = "calling"
        elif latest_call and latest_call.get("ended_at"):
            status = latest_call.get("outcome") or "failed"
        elif company["id"] in _queued.get(job_id, set()):
            status = "queued"
        else:
            status = "to_call"
        transcript = (latest_call or {}).get("transcript", [])
        latest_turn = transcript[-1] if isinstance(transcript, list) and transcript else None
        if not isinstance(latest_turn, dict):
            latest_turn = None
        turn_preview = ({
            "role": str(latest_turn.get("role", ""))[:32],
            "text": str(latest_turn.get("text", ""))[:240],
        } if latest_turn else None)
        rows.append({
            "company": {
                "id": company["id"], "name": company["name"],
                "phone": company.get("phone", ""), "persona": company.get("persona", ""),
                "source": company.get("source", ""),
                "discovery_sources": company.get("discovery_sources", []),
            },
            "status": status,
            "last_call_kind": (latest_call or {}).get("kind", ""),
            "conversation_id": (latest_call or {}).get("conversation_id", ""),
            "initial_total": initial["total"] if initial else None,
            "negotiated_total": negotiated["total"] if negotiated else None,
            "red_flags": (negotiated or initial or {}).get("red_flags", []),
            "attempt_count": len(company_calls),
            "recalls_used": len(recall_reservations_for_company(job_id, company["id"])),
            "recalls_max": config.MAX_VENDOR_RECALLS,
            "batch_index": (latest_call or {}).get("batch_index"),
            "knowledge_version": (latest_call or {}).get("knowledge_version"),
            "dial_mode": (latest_call or {}).get("mode", ""),
            "transcript_kind": (latest_call or {}).get("transcript_kind", ""),
            "transcript_streaming": bool((latest_call or {}).get("transcript_streaming")),
            "transcript_turn_count": int((latest_call or {}).get(
                "transcript_turn_count", len(transcript) if isinstance(transcript, list) else 0
            )),
            "last_transcript_at": (latest_call or {}).get("last_transcript_at", ""),
            "last_transcript_turn": turn_preview,
            "follow_up": plans.get(company["id"]),
        })

    runs = db.where("call_runs", job_id=job_id)
    run = _latest(runs)
    batches = db.where("call_batches", job_id=job_id)
    run_batches = [b for b in batches if run and b.get("run_id") == run["id"]]
    active_batch = next((b for b in run_batches if b.get("status") in {"queued", "running"}), None)
    if not active_batch:
        active_batch = _latest(run_batches)

    offers = latest_offers(job_id, completed_only=False)
    # Prefer the latest negotiated offer, otherwise initial, once per vendor.
    by_company: dict[str, dict] = {}
    for q in offers:
        current = by_company.get(q["company_id"])
        if current is None or q.get("phase") == "negotiated":
            by_company[q["company_id"]] = q
    all_final_offers = list(by_company.values())
    final_offers = [q for q in all_final_offers
                    if q.get("evidence_verified") and q.get("grounding_verified")]
    safe = [q for q in final_offers
            if not any(flag.get("severity") == "high" for flag in q.get("red_flags", []))]
    candidates = safe or final_offers
    best = min(candidates, key=lambda q: q["total"]) if candidates else None
    company_map = {c["id"]: c for c in companies}
    call_map = {call["id"]: call for call in calls}
    totals = [q["total"] for q in final_offers]
    benchmark_range = None
    if job and job.get("spec"):
        try:
            from .benchmarks import market_range
            pack = load_pack(job["vertical"], job.get("area_code", ""))
            bench = market_range(job["spec"], pack)
            benchmark_range = {"low": bench["fair_low"], "median": bench["median"],
                               "high": bench["fair_high"],
                               "red_flag_floor": bench["red_flag_floor"]}
        except Exception:
            pass

    # Market coverage remains stable when a later one-vendor negotiation run
    # starts. When Google discovery exists, N is the entire unique callable
    # Google market, not whichever subset a client happened to put in a run.
    quote_runs = [candidate for candidate in runs if candidate.get("phase") == "quote"]
    google_ids = {c["id"] for c in companies if c.get("source") == "google_places"
                  and c.get("phone")}
    market_run = (max(quote_runs, key=lambda candidate: (
        len(candidate.get("company_ids", [])), candidate.get("created_at", "")))
        if quote_runs else None)
    if google_ids:
        market_ids = google_ids
    elif market_run:
        market_ids = set(market_run.get("company_ids", []))
    else:
        market_ids = {c["id"] for c in companies}
    called = len({c["company_id"] for c in calls
                  if c.get("kind") == "quote" and c.get("ended_at")
                  and c.get("company_id") in market_ids})
    total = len(market_ids)

    running = bool(run and run.get("status") in {"queued", "running"}) or (
        job_id in _runs and _runs[job_id].is_alive()
    ) or any(row["status"] in {"queued", "calling"} for row in rows)
    return {
        "debug_mode": config.DEBUG_CALLS,
        "debug_behavior": ("hybrid_demo_roleplay" if _demo_mode(job) else
                           ("transcript_only" if config.DEBUG_CALLS else
                            "voice_and_telephony")),
        "running": running,
        "demo_mode": ({
            "roleplay": True,
            "session_id": _demo_mode(job).get("session_id", ""),
            "live_company_id": _demo_mode(job).get("live_company_id", ""),
            "live_company_name": _demo_mode(job).get("live_company_name", ""),
            "auto_negotiate": bool(_demo_mode(job).get("auto_negotiate")),
            "quote_batch_count": (run or {}).get("quote_batch_count"),
            "auto_negotiation_batch": (run or {}).get("auto_negotiation_batch"),
            "auto_negotiation_status": (run or {}).get("auto_negotiation_status",
                                                          "not_started"),
            "demo_calls_authorized": bool((run or {}).get("demo_calls_authorized")),
            "destination": "configured_demo_phone",
            "notice": (
                "One Google lead is routed to the allow-listed human. Other offers are "
                "synthetic demo-market data and must be described as simulated."
            ),
        } if _demo_mode(job) else None),
        "summary": {
            "current_best_offer": ({
                "company_id": best["company_id"],
                "company_name": company_map.get(best["company_id"], {}).get("name", "Unknown vendor"),
                "quote_id": best["id"], "total": best["total"],
                "binding": best.get("binding", False),
                "red_flags": best.get("red_flags", []),
                "evidence_kind": best.get("evidence_kind", ""),
                "synthetic": best.get("evidence_kind") == "debug_generated",
                "demo_roleplay": bool(
                    best.get("evidence_kind") == "demo_roleplay_voice"
                    or call_map.get(best.get("call_id", ""), {}).get("demo_roleplay")
                ),
            } if best else None),
            "offer_range": ({"low": min(totals), "high": max(totals), "count": len(totals)}
                            if totals else None),
            "excluded_unverified_offers": len(all_final_offers) - len(final_offers),
            "benchmark_range": benchmark_range,
            "called": called,
            "total": total,
            "calling": sum(1 for row in rows if row["status"] == "calling"),
        },
        "batch": ({
            "run_id": run.get("id"),
            "index": active_batch.get("index") if active_batch else None,
            "count": run.get("batch_count"),
            "quote_batch_count": run.get("quote_batch_count", run.get("batch_count")),
            "auto_negotiation_batch": run.get("auto_negotiation_batch"),
            "auto_negotiation_status": run.get("auto_negotiation_status", "not_requested"),
            "phase": (active_batch or {}).get("phase", run.get("phase")),
            "size": (len(active_batch.get("company_ids", []))
                     if active_batch else run.get("batch_size")),
            "status": active_batch.get("status") if active_batch else run.get("status"),
            "knowledge_version": (active_batch or {}).get("knowledge_version",
                                                            run.get("knowledge_version", 0)),
            "completed": (active_batch or {}).get("completed", 0),
            "total": len((active_batch or {}).get("company_ids", [])),
        } if run else None),
        "follow_up_plan": list(plans.values()),
        "queue": rows,
    }


def start_calls(job_id: str, phase: str, company_ids: list[str] | None = None,
                parallel: bool | None = None, retry_completed: bool = False,
                recommended_only: bool = False,
                idempotency_key: str | None = None,
                authorize_demo_calls: bool = False) -> dict:
    """Start a background run.  ``parallel`` is accepted for old clients but
    ignored: concurrency is always the server-computed sqrt(n) policy."""
    job = db.get("jobs", job_id)
    if not job:
        raise LookupError("job not found")
    if job.get("archived"):
        raise RuntimeError("Archived jobs are read-only. Reset the demo to create a fresh job.")
    if idempotency_key:
        from .runclaims import claim_run, find_idempotent_run, finish_run
        replay = find_idempotent_run(job_id, idempotency_key)
        if replay:
            # Completed/non-expired requests are pure replays. An expired
            # active request is reclaimed only to fence and terminalize its
            # unknown external state; it is never silently re-dialled.
            if replay.status != "active" or replay.lease_expires_at > time.time():
                return _idempotent_run_response(replay.run_id, phase, replay.status)
            lease_seconds = max(config.CALL_RUN_LEASE_SECS,
                                config.CALL_BATCH_TIMEOUT_SECS + 120)
            reclaimed = claim_run(
                job_id, run_id=replay.run_id, idempotency_key=idempotency_key,
                stale_after=lease_seconds,
                metadata={"phase": phase, "recovery": "fail_closed"},
            )
            if not reclaimed.acquired:
                return _idempotent_run_response(reclaimed.run_id, phase, reclaimed.status)
            stale = db.get("call_runs", reclaimed.run_id)
            if stale:
                stale.update({
                    "status": "failed", "ended_at": now(),
                    "error": "Run lease expired; automatic redial was suppressed.",
                })
                db.put("call_runs", stale["id"], stale, job_id=job_id)
                _fail_unfinished(job_id, stale["id"], stale["error"])
            finish_run(job_id, reclaimed.run_id, reclaimed.owner_token, "failed")
            raise RuntimeError(
                "The prior idempotent run expired; automatic redial was suppressed. "
                "Inspect outcomes, then retry explicitly with a new idempotency_key."
            )
    demo = _demo_mode(job)
    if demo and not authorize_demo_calls:
        raise RuntimeError(
            "This prepared demo places two calls to the allow-listed role-player. "
            "Start it only after explicit confirmation with authorize_demo_calls=true."
        )
    google = sync_google_companies(job_id)
    companies = google or db.where("companies", job_id=job_id)
    if demo and phase != "quote":
        raise RuntimeError(
            "Prepared demo runs start in quote phase; the grounded negotiation "
            "callback is appended automatically after every quote barrier."
        )
    # Initial Google-market coverage is deliberately all-or-nothing: a client
    # cannot accidentally reintroduce top-N sampling. Explicit subsets are
    # reserved for negotiation or an intentional retry.
    if company_ids and not (phase == "quote" and not retry_completed):
        wanted = set(company_ids)
        companies = [c for c in companies if c["id"] in wanted]
    if phase == "negotiate":
        quoted = {q["company_id"] for q in db.where("quotes", job_id=job_id)
                  if q.get("evidence_verified") and q.get("grounding_verified")}
        companies = [c for c in companies if c["id"] in quoted]
        if recommended_only:
            recommended = {p["company_id"] for p in job.get("follow_up_plan", [])
                           if p.get("status") == "recommended"}
            companies = [c for c in companies if c["id"] in recommended]
    elif not retry_completed:
        called = {c["company_id"] for c in db.where("calls", job_id=job_id)
                  if c.get("kind") == "quote" and c.get("ended_at")}
        companies = [c for c in companies if c["id"] not in called]
    if demo:
        if not job.get("documents") or "document" not in job.get("spec_source", ""):
            raise RuntimeError(
                "Prepared demo intake is incomplete: upload the supplied document first."
            )
        target_id = demo.get("live_company_id", "")
        target = next((company for company in companies if company["id"] == target_id), None)
        if not target:
            raise LookupError(
                "The configured live demo vendor is not eligible. Reset the demo session "
                "instead of retrying or changing the target mid-run."
            )
        # The exploratory human call belongs to batch one.  The callback is a
        # separate final batch created only after every quote batch has crossed
        # its barrier and published its knowledge.
        companies = [target] + [company for company in companies if company["id"] != target_id]
    if not companies:
        raise LookupError("No eligible companies" +
                          (" (gather quotes before negotiating)." if phase == "negotiate"
                           else " (discover Google Places vendors first, or enable retry)."))

    all_calls = db.where("calls", job_id=job_id)
    uncertain_company_ids = {call.get("company_id") for call in all_calls
                             if call.get("external_state_uncertain")
                             and not call.get("external_state_resolved_at")}
    uncertain = [company["name"] for company in companies
                 if company["id"] in uncertain_company_ids]
    if uncertain:
        raise RuntimeError(
            "Provider state is still unconfirmed; automatic redial is locked for: "
            + ", ".join(uncertain[:5])
            + ". Reconcile the prior ElevenLabs batch manually before any retry."
        )
    active_company_ids = {call.get("company_id") for call in all_calls
                          if call.get("status") not in TERMINAL_CALL_STATUSES}
    overlapping = [company["name"] for company in companies
                   if company["id"] in active_company_ids]
    if overlapping:
        raise RuntimeError(
            "Wait for the active vendor attempt(s) to finish before scheduling another call: "
            + ", ".join(overlapping[:5])
        )
    previously_attempted = {call.get("company_id") for call in all_calls}

    _validate_runtime(job, companies)
    run_id = db.new_id("run")
    from .runclaims import claim_run, finish_run
    lease_seconds = max(config.CALL_RUN_LEASE_SECS, config.CALL_BATCH_TIMEOUT_SECS + 120)
    claim = claim_run(
        job_id,
        run_id=run_id,
        idempotency_key=idempotency_key,
        stale_after=lease_seconds,
        metadata={"phase": phase, "company_ids": [c["id"] for c in companies]},
    )
    if not claim.acquired:
        if claim.reason.startswith("idempotent_"):
            return _idempotent_run_response(claim.run_id, phase, claim.status)
        raise RuntimeError("Calls already running for this job.")
    # Never silently re-dial an expired idempotent request. Its external state
    # may be unknowable after a crash; fail closed and require a fresh,
    # intentional retry key.
    if claim.restarted and claim.run_id == claim.previous_run_id \
            and db.get("call_runs", claim.run_id):
        stale = db.get("call_runs", claim.run_id)
        stale.update({"status": "failed", "ended_at": now(),
                      "error": "Run lease expired; automatic redial was suppressed."})
        db.put("call_runs", claim.run_id, stale, job_id=job_id)
        _fail_unfinished(job_id, claim.run_id, stale["error"])
        finish_run(job_id, claim.run_id, claim.owner_token, "failed")
        raise RuntimeError(
            "The prior idempotent run expired; automatic redial was suppressed. "
            "Inspect outcomes, then retry explicitly with a new idempotency_key."
        )
    if claim.previous_run_id and claim.previous_run_id != claim.run_id:
        previous = db.get("call_runs", claim.previous_run_id)
        if previous and previous.get("status") in {"queued", "running"}:
            previous.update({"status": "failed", "ended_at": now(),
                             "error": "Stale run lease was replaced by an explicit new request."})
            db.put("call_runs", previous["id"], previous, job_id=job_id)
            _fail_unfinished(job_id, previous["id"], previous["error"])
    from .recall_limits import reserve as reserve_recall
    recall_reservations: dict[str, dict] = {}
    eligible_after_limit = []
    for company in companies:
        is_recall = phase == "negotiate" or company["id"] in previously_attempted
        if not is_recall:
            eligible_after_limit.append(company)
            continue
        reservation_id = f"{run_id}:{company['id']}"
        slot = reserve_recall(
            job_id, company["id"], reservation_id,
            max_recalls=config.MAX_VENDOR_RECALLS,
            status="reserved",
            metadata={"run_id": run_id, "phase": phase},
        )
        if slot is None:
            continue
        recall_reservations[company["id"]] = {
            "reservation_id": reservation_id, "slot": slot,
        }
        eligible_after_limit.append(company)
    companies = eligible_after_limit
    if not companies:
        finish_run(job_id, run_id, claim.owner_token, "failed")
        raise LookupError(
            f"All eligible vendors reached the hard limit of {config.MAX_VENDOR_RECALLS} recalls."
        )
    size = computed_batch_size(len(companies))
    chunks = _chunks(companies, size)
    auto_negotiate = bool(demo and demo.get("auto_negotiate"))
    quote_batch_count = len(chunks)
    auto_negotiation_batch = quote_batch_count + 1 if auto_negotiate else None
    frozen_spec = json.loads(json.dumps(job.get("spec", {})))
    try:
        initial_snapshot = create_snapshot(
            job_id, int(job.get("knowledge_version", 0)),
            allow_debug_leverage=bool(config.DEBUG_CALLS or demo),
        )
    except Exception:
        finish_run(job_id, run_id, claim.owner_token, "failed")
        raise
    run = {
        "id": run_id, "job_id": job_id, "phase": phase,
        "status": "queued",
        "mode": ("demo_roleplay" if demo else
                 ("debug_transcript" if config.DEBUG_CALLS else "voice")),
        "company_ids": [c["id"] for c in companies],
        "total": len(companies), "completed": 0,
        "total_calls": len(companies) + int(auto_negotiate),
        "quote_total": len(companies), "quote_completed": 0,
        "batch_size": size,
        "quote_batch_count": quote_batch_count,
        "batch_count": quote_batch_count + int(auto_negotiate),
        "auto_negotiation_batch": auto_negotiation_batch,
        "auto_negotiation_status": ("waiting_for_quote_batches"
                                    if auto_negotiate else "not_requested"),
        "demo_calls_authorized": bool(demo and authorize_demo_calls),
        "knowledge_version": int(job.get("knowledge_version", 0)),
        "spec": frozen_spec, "spec_hash": spec_hash(frozen_spec),
        "document_offers": [offer for offer in initial_snapshot.get("offers", [])
                            if offer.get("phase") == "document"],
        "recall_reservations": recall_reservations,
        "demo_mode": json.loads(json.dumps(demo)) if demo else {},
        "created_at": now(),
    }
    run["idempotency_key"] = idempotency_key or ""
    run["claim_generation"] = claim.generation
    db.put("call_runs", run_id, run, job_id=job_id)
    _queued[job_id] = set(run["company_ids"])

    thread = threading.Thread(target=_execute_run,
                              args=(run_id, chunks, claim.owner_token, lease_seconds), daemon=True,
                              name=f"quotewise-{run_id}")
    _runs[job_id] = thread
    try:
        thread.start()
    except Exception:
        _runs.pop(job_id, None)
        _queued.pop(job_id, None)
        finish_run(job_id, run_id, claim.owner_token, "failed")
        raise
    return {
        "started": True, "run_id": run_id, "phase": phase,
        "debug_mode": config.DEBUG_CALLS, "batch_size": size,
        "batch_count": quote_batch_count + int(auto_negotiate),
        "quote_batch_count": quote_batch_count,
        "auto_negotiation_batch": auto_negotiation_batch,
        "auto_negotiation_status": ("waiting_for_quote_batches"
                                    if auto_negotiate else "not_requested"),
        "total": len(companies),
        "total_calls": len(companies) + int(auto_negotiate),
        "companies": [{"id": c["id"], "name": c["name"]} for c in companies],
        "demo_roleplay": bool(demo),
        "live_company_id": demo.get("live_company_id", "") if demo else "",
        "live_destination": "configured_demo_phone" if demo else "",
        "demo_calls_authorized": bool(demo and authorize_demo_calls),
    }


def _idempotent_run_response(run_id: str, fallback_phase: str, claim_status: str) -> dict:
    existing = db.get("call_runs", run_id) or {}
    return {
        "started": False, "idempotent_replay": True,
        "run_id": run_id, "phase": existing.get("phase", fallback_phase),
        "status": existing.get("status", claim_status),
        "debug_mode": config.DEBUG_CALLS,
        "batch_size": existing.get("batch_size"),
        "batch_count": existing.get("batch_count"),
        "quote_batch_count": existing.get("quote_batch_count", existing.get("batch_count")),
        "auto_negotiation_batch": existing.get("auto_negotiation_batch"),
        "auto_negotiation_status": existing.get("auto_negotiation_status", "not_requested"),
        "total": existing.get("total"),
        "total_calls": existing.get("total_calls", existing.get("total")),
    }


def _validate_runtime(job: dict, companies: list[dict]):
    demo = _demo_mode(job)
    if demo:
        target_id = demo.get("live_company_id", "")
        target = next((company for company in companies if company["id"] == target_id), None)
        if not target or target.get("source") != "google_places" \
                or not target.get("external_ids", {}).get("google_places"):
            raise RuntimeError("Demo target must be a real Google Places lead on this job.")
        if not config.DEMO_PHONE_NUMBER:
            raise RuntimeError("DEMO_PHONE_NUMBER missing — the role-play destination is not configured.")
        if not config.ELEVENLABS_PHONE_NUMBER_ID:
            raise RuntimeError("ELEVENLABS_PHONE_NUMBER_ID missing — import the Twilio number first.")
        if not config.ELEVENLABS_API_KEY or not config.registry_path().exists():
            raise RuntimeError("ElevenLabs is not configured/provisioned for the demo role-play.")
        _validate_agent_registry(job)
        # LIVE_VENDOR_CALLS_ENABLED is intentionally irrelevant here: every
        # non-target lead is transcript-only and the sole phone destination is
        # the server-side allow-listed human.
        return
    if config.DEBUG_CALLS:
        return
    if not config.ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY missing — real calls are disabled.")
    if not config.registry_path().exists():
        raise RuntimeError("Agents not provisioned — run `python -m agents.provision`.")
    _validate_agent_registry(job)
    if any(c.get("source") == "google_places" for c in companies):
        if not config.LIVE_VENDOR_CALLS_ENABLED:
            raise RuntimeError(
                "Real vendor calls require LIVE_VENDOR_CALLS_ENABLED=true in addition to DEBUG_CALLS=false."
            )
        if not config.ELEVENLABS_PHONE_NUMBER_ID:
            raise RuntimeError("ELEVENLABS_PHONE_NUMBER_ID missing — import a Twilio number in ElevenLabs.")


def _validate_agent_registry(job: dict):
    if not config.registry_path().exists():
        raise RuntimeError("Agents not provisioned — run `python -m agents.provision`.")
    registry = json.loads(config.registry_path().read_text())
    meta = registry.get("meta", {})
    provisioned_vertical = meta.get("vertical", "")
    if provisioned_vertical != job.get("vertical"):
        raise RuntimeError(
            f"Live agents are provisioned for {provisioned_vertical or 'an unknown legacy vertical'}, "
            f"not {job.get('vertical')}. Set VERTICAL and re-run `python -m agents.provision`."
        )
    from agents import prompts
    if meta.get("schema_version") != prompts.PROMPT_SCHEMA_VERSION \
            or meta.get("prompt_revision") != prompts.prompt_revision():
        raise RuntimeError(
            "Live agent prompts are stale or unverifiable. Re-run `python -m agents.provision` "
            "before authorising telephony."
        )


def _execute_run(run_id: str, chunks: list[list[dict]], owner_token: str,
                 lease_seconds: float):
    run = db.get("call_runs", run_id)
    job_id = run["job_id"]
    from .runclaims import finish_run, heartbeat_run
    claim_status = "failed"
    try:
        if not heartbeat_run(job_id, run_id, owner_token, stale_after=lease_seconds):
            raise RuntimeError("Call-run ownership was lost before execution started.")
        run["status"] = "running"
        run["started_at"] = now()
        db.put("call_runs", run_id, run, job_id=job_id)
        for index, companies in enumerate(chunks, start=1):
            if not heartbeat_run(job_id, run_id, owner_token, stale_after=lease_seconds):
                raise RuntimeError("Call-run ownership was lost; refusing to start another batch.")
            # Offers are refreshed; the job spec stays frozen for the entire run.
            snapshot = create_snapshot(
                job_id, run["knowledge_version"],
                allow_debug_leverage=bool(config.DEBUG_CALLS or run.get("demo_mode")),
            )
            snapshot["offers"] = ([offer for offer in snapshot.get("offers", [])
                                   if offer.get("phase") != "document"]
                                  + run.get("document_offers", []))
            snapshot["spec"] = run["spec"]
            snapshot["spec_hash"] = run["spec_hash"]
            pack = load_pack((db.get("jobs", job_id) or {}).get("vertical", "moving"),
                             (db.get("jobs", job_id) or {}).get("area_code", ""))
            from .benchmarks import market_range
            snapshot["benchmark"] = market_range(run["spec"], pack)
            batch_id = db.new_id("batch")
            batch = {
                "id": batch_id, "run_id": run_id, "job_id": job_id,
                "index": index, "status": "running", "company_ids": [c["id"] for c in companies],
                "phase": run["phase"], "quote_batch": True,
                "quote_batch_count": run.get("quote_batch_count", len(chunks)),
                "knowledge_version": run["knowledge_version"], "knowledge_snapshot": snapshot,
                "completed": 0, "created_at": now(), "started_at": now(),
            }
            db.put("call_batches", batch_id, batch, job_id=job_id, run_id=run_id)
            call_rows = []
            for company in companies:
                mode = _mode_for(company, run)
                call_id = db.new_id("call")
                attempts = len(db.where("calls", job_id=job_id, company_id=company["id"])) + 1
                context = context_for(snapshot, company["id"])
                demo_roleplay = bool(run.get("demo_mode"))
                call = {
                    "id": call_id, "job_id": job_id, "company_id": company["id"],
                    "kind": run["phase"], "run_id": run_id, "batch_id": batch_id,
                    "batch_index": index, "knowledge_version": snapshot["version"],
                    "knowledge_snapshot": context,
                    "spec_hash": run["spec_hash"], "attempt_number": attempts,
                    "mode": mode, "status": "queued", "created_at": now(),
                }
                execution_company = company
                if demo_roleplay:
                    call.update({
                        "demo_roleplay": True,
                        "demo_session_id": run["demo_mode"].get("session_id", ""),
                        "counterparty_setup": ("human_roleplay" if mode == "demo_phone"
                                               else "synthetic_transcript"),
                    })
                if mode == "demo_phone":
                    # The Google company record remains byte-for-byte the
                    # market identity. Only this ephemeral transport copy is
                    # routed to the consenting allow-listed human.
                    execution_company = {**company, "phone": config.DEMO_PHONE_NUMBER}
                    call["dialed_to"] = "configured_demo_phone"
                recall = run.get("recall_reservations", {}).get(company["id"])
                if recall:
                    call["recall_reservation_id"] = recall["reservation_id"]
                    call["recall_slot"] = recall["slot"]
                db.put("calls", call_id, call, job_id=job_id, company_id=company["id"])
                if recall:
                    from .recall_limits import attach_call
                    if not attach_call(job_id, company["id"], recall["reservation_id"],
                                       call_id, status="queued"):
                        raise RuntimeError("Recall reservation disappeared before call creation.")
                call_rows.append((call, execution_company))
            batch_error = None
            try:
                _execute_batch(call_rows, batch)
            except Exception as exc:
                # Workers already terminalize their calls. Preserve the error,
                # but always close the batch record and publish the end-of-batch
                # knowledge version before stopping the run.
                batch_error = exc

            # Hard barrier: _execute_batch returns/raises only after every
            # worker joined and every affected call was terminalized.
            terminal = [db.get("calls", call["id"]) for call, _ in call_rows]
            batch = db.get("call_batches", batch_id)
            batch["completed"] = sum(
                1 for c in terminal if c and c.get("status") in TERMINAL_CALL_STATUSES)
            batch["succeeded"] = sum(1 for c in terminal if c and c.get("status") == "completed")
            batch["failed"] = sum(1 for c in terminal if c and c.get("status") == "failed")
            if batch["completed"] != len(call_rows):
                batch["status"] = "failed"
            elif batch["failed"] and batch["succeeded"]:
                batch["status"] = "completed_with_failures"
            elif batch["failed"]:
                batch["status"] = "failed"
            else:
                batch["status"] = "completed"
            batch["ended_at"] = now()
            db.put("call_batches", batch_id, batch, job_id=job_id, run_id=run_id)

            run = db.get("call_runs", run_id)
            run["completed"] = run.get("completed", 0) + batch["completed"]
            run["quote_completed"] = run.get("quote_completed", 0) + batch["completed"]
            new_version, _ = _advance_knowledge(job_id)
            run["knowledge_version"] = new_version
            db.put("call_runs", run_id, run, job_id=job_id)
            if not heartbeat_run(job_id, run_id, owner_token, stale_after=lease_seconds):
                raise RuntimeError("Call-run ownership was lost after the batch barrier.")
            if batch_error is not None:
                raise batch_error

        run = db.get("call_runs", run_id)
        if run.get("auto_negotiation_batch"):
            run["quote_barrier_completed_at"] = now()
            db.put("call_runs", run_id, run, job_id=job_id)
            _execute_auto_demo_negotiation(run_id, owner_token, lease_seconds)

        run = db.get("call_runs", run_id)
        finished_batches = [b for b in db.where("call_batches", job_id=job_id)
                            if b.get("run_id") == run_id]
        run["status"] = ("completed_with_failures"
                         if any(b.get("status") != "completed" for b in finished_batches)
                         else "completed")
        run["ended_at"] = now()
        db.put("call_runs", run_id, run, job_id=job_id)
        claim_status = "completed"
    except Exception as exc:
        run = db.get("call_runs", run_id)
        run["status"] = "failed"
        run["error"] = f"{type(exc).__name__}: {exc}"
        run["ended_at"] = now()
        if run.get("auto_negotiation_batch") and run.get("auto_negotiation_status") \
                in {"waiting_for_quote_batches", "running"}:
            run["auto_negotiation_status"] = "failed"
            run["auto_negotiation_ended_at"] = run["ended_at"]
            run["auto_negotiation_error"] = str(exc)[:300]
        db.put("call_runs", run_id, run, job_id=job_id)
        _fail_unfinished(job_id, run_id, str(exc))
    finally:
        finish_run(job_id, run_id, owner_token, claim_status)
        _queued.pop(job_id, None)
        _runs.pop(job_id, None)


def _execute_auto_demo_negotiation(run_id: str, owner_token: str,
                                   lease_seconds: float) -> None:
    """Append the authorised live callback as the final batch of a quote run.

    This deliberately reuses the quote run's durable lease.  It is therefore
    impossible for another scheduler to enter between the final quote barrier
    and the callback snapshot.  The callback is created only after all quote
    calls are terminal and the last quote knowledge version has been
    published.
    """
    from .runclaims import heartbeat_run
    from .recall_limits import attach_call, reserve as reserve_recall

    run = db.get("call_runs", run_id)
    if not run:
        raise RuntimeError("Demo run disappeared before its automatic negotiation.")
    job_id = run["job_id"]
    demo = run.get("demo_mode") or {}
    if not (demo.get("roleplay") and demo.get("auto_negotiate")):
        return
    if not run.get("demo_calls_authorized"):
        raise RuntimeError("Automatic demo callback lacks explicit operator authorisation.")
    if not heartbeat_run(job_id, run_id, owner_token, stale_after=lease_seconds):
        raise RuntimeError("Call-run ownership was lost before the automatic callback.")

    quote_calls = [
        call for call in db.where("calls", job_id=job_id)
        if call.get("run_id") == run_id and call.get("kind") == "quote"
    ]
    if len(quote_calls) != int(run.get("quote_total", run.get("total", 0))) or any(
            call.get("status") not in TERMINAL_CALL_STATUSES for call in quote_calls):
        raise RuntimeError(
            "Automatic negotiation is blocked until every quote call is terminal."
        )

    target_id = demo.get("live_company_id", "")
    company = db.get("companies", target_id)
    if not company or company.get("source") != "google_places" \
            or not company.get("external_ids", {}).get("google_places"):
        raise RuntimeError("The preselected Google demo target is no longer available.")
    target_quote = next((
        quote for quote in reversed(sorted(
            db.where("quotes", job_id=job_id, company_id=target_id),
            key=lambda row: row.get("created_at", ""),
        ))
        if quote.get("phase") == "initial"
        and quote.get("evidence_verified")
        and quote.get("grounding_verified")
        and quote.get("itemization_verified") is True
        and quote.get("evidence_kind") != "debug_generated"
    ), None)
    if not target_quote:
        raise RuntimeError(
            "The exploratory role-player call produced no verified itemised quote; "
            "automatic negotiation is blocked."
        )

    version = int(run.get("knowledge_version", 0))
    snapshot = create_snapshot(job_id, version, allow_debug_leverage=True)
    snapshot["offers"] = ([offer for offer in snapshot.get("offers", [])
                           if offer.get("phase") != "document"]
                          + run.get("document_offers", []))
    snapshot["spec"] = run["spec"]
    snapshot["spec_hash"] = run["spec_hash"]
    job = db.get("jobs", job_id) or {}
    pack = load_pack(job.get("vertical", "moving"), job.get("area_code", ""))
    from .benchmarks import market_range
    snapshot["benchmark"] = market_range(run["spec"], pack)
    context = context_for(snapshot, target_id)
    if not context.get("allowed_competitive_claims"):
        raise RuntimeError(
            "No grounded competing demo-market offer exists after the quote barrier."
        )

    batch_index = int(run["auto_negotiation_batch"])
    reservation_id = f"{run_id}:{target_id}:auto-negotiate"
    slot = reserve_recall(
        job_id, target_id, reservation_id,
        max_recalls=config.MAX_VENDOR_RECALLS,
        status="reserved",
        metadata={"run_id": run_id, "phase": "negotiate", "automatic": True},
    )
    if slot is None:
        raise RuntimeError(
            f"Automatic callback blocked by the hard limit of {config.MAX_VENDOR_RECALLS} recalls."
        )

    batch_id = db.new_id("batch")
    call_id = db.new_id("call")
    batch = {
        "id": batch_id, "run_id": run_id, "job_id": job_id,
        "index": batch_index, "status": "running", "company_ids": [target_id],
        "phase": "negotiate", "auto_negotiation": True,
        "quote_batch_count": run.get("quote_batch_count"),
        "knowledge_version": version, "knowledge_snapshot": snapshot,
        "completed": 0, "created_at": now(), "started_at": now(),
    }
    call = {
        "id": call_id, "job_id": job_id, "company_id": target_id,
        "kind": "negotiate", "run_id": run_id, "batch_id": batch_id,
        "batch_index": batch_index, "knowledge_version": version,
        "knowledge_snapshot": context, "spec_hash": run["spec_hash"],
        "attempt_number": len(db.where("calls", job_id=job_id,
                                        company_id=target_id)) + 1,
        "mode": "demo_phone", "status": "queued", "created_at": now(),
        "dialed_to": "configured_demo_phone", "demo_roleplay": True,
        "demo_session_id": demo.get("session_id", ""),
        "counterparty_setup": "human_roleplay", "auto_negotiation": True,
        "recall_reservation_id": reservation_id, "recall_slot": slot,
    }
    run["auto_negotiation_status"] = "running"
    run["auto_negotiation_started_at"] = now()
    run["auto_negotiation_call_id"] = call_id
    run["auto_negotiation_batch_id"] = batch_id
    run.setdefault("recall_reservations", {})[target_id] = {
        "reservation_id": reservation_id, "slot": slot,
    }
    db.put("call_runs", run_id, run, job_id=job_id)
    db.put("call_batches", batch_id, batch, job_id=job_id, run_id=run_id)
    db.put("calls", call_id, call, job_id=job_id, company_id=target_id)
    if not attach_call(job_id, target_id, reservation_id, call_id, status="queued"):
        raise RuntimeError("Automatic recall reservation disappeared before call creation.")
    _queued.setdefault(job_id, set()).add(target_id)

    batch_error = None
    try:
        # Never pass the stored Google phone across the provider boundary.
        destination = {**company, "phone": config.DEMO_PHONE_NUMBER}
        _execute_batch([(call, destination)], batch)
    except Exception as exc:
        batch_error = exc

    terminal = db.get("calls", call_id) or {}
    batch = db.get("call_batches", batch_id) or batch
    batch.update({
        "completed": int(terminal.get("status") in TERMINAL_CALL_STATUSES),
        "succeeded": int(terminal.get("status") == "completed"),
        "failed": int(terminal.get("status") == "failed"),
        "status": "completed" if terminal.get("status") == "completed" else "failed",
        "ended_at": now(),
    })
    db.put("call_batches", batch_id, batch, job_id=job_id, run_id=run_id)
    new_version, _ = _advance_knowledge(job_id)
    run = db.get("call_runs", run_id)
    run["completed"] = run.get("completed", 0) + batch["completed"]
    run["knowledge_version"] = new_version
    run["auto_negotiation_status"] = (
        "completed" if terminal.get("status") == "completed" else "failed"
    )
    run["auto_negotiation_ended_at"] = now()
    db.put("call_runs", run_id, run, job_id=job_id)
    if not heartbeat_run(job_id, run_id, owner_token, stale_after=lease_seconds):
        raise RuntimeError("Call-run ownership was lost after the automatic callback barrier.")
    if batch_error is not None:
        raise batch_error
    if terminal.get("status") != "completed":
        raise RuntimeError("Automatic demo negotiation did not complete successfully.")


def _mode_for(company: dict, run: dict | None = None) -> str:
    demo = (run or {}).get("demo_mode") or {}
    if demo:
        if company.get("id") == demo.get("live_company_id"):
            return "demo_phone"
        return "debug_transcript"
    if config.DEBUG_CALLS:
        return "debug_transcript"
    if company.get("source") == "google_places":
        return "twilio_vendor"
    return "agent_bridge"


def _execute_batch(call_rows: list[tuple[dict, dict]], batch: dict):
    groups: dict[str, list[tuple[dict, dict]]] = {}
    for row in call_rows:
        groups.setdefault(row[0]["mode"], []).append(row)
    workers = []
    errors: list[Exception] = []
    errors_lock = threading.Lock()
    for mode, rows in groups.items():
        target = {
            "debug_transcript": _run_debug_group,
            "twilio_vendor": _run_twilio_batch,
            "demo_phone": _run_twilio_batch,
            "agent_bridge": _run_bridge_group,
        }[mode]
        worker = threading.Thread(target=_guard_group,
                                  args=(target, rows, batch, errors, errors_lock), daemon=True)
        worker.start()
        workers.append(worker)
    for worker in workers:
        worker.join()
    if errors:
        raise RuntimeError("; ".join(f"{type(exc).__name__}: {exc}" for exc in errors))


def _guard_group(target, rows, batch, errors, errors_lock):
    try:
        target(rows, batch)
    except Exception as exc:
        for call, _ in rows:
            _fail_call(call["id"], f"{type(exc).__name__}: {exc}",
                       reason="batch_worker_error")
        with errors_lock:
            errors.append(exc)


def _set_calling(call_id: str):
    call = db.get("calls", call_id)
    call["status"] = "calling"
    call["started_at"] = now()
    db.put("calls", call_id, call, job_id=call["job_id"], company_id=call["company_id"])
    if call.get("recall_reservation_id"):
        from .recall_limits import set_status
        set_status(call["job_id"], call["company_id"], call["recall_reservation_id"], "calling")
    _queued.get(call["job_id"], set()).discard(call["company_id"])


def _run_debug_group(rows: list[tuple[dict, dict]], batch: dict):
    workers = []
    for call, company in rows:
        def debug_one(call=call, company=company):
            try:
                _run_debug_one(call["id"], company)
            except Exception as exc:
                _fail_call(call["id"], f"{type(exc).__name__}: {exc}")
        worker = threading.Thread(target=debug_one, daemon=True)
        worker.start()
        workers.append(worker)
    for worker in workers:
        worker.join()


def _run_debug_one(call_id: str, company: dict):
    from .debugcalls import generate_debug_result
    from .learnings import persist_questions, questions_from_call

    _set_calling(call_id)
    call = db.get("calls", call_id)
    job = db.get("jobs", call["job_id"])
    pack = load_pack(job["vertical"], job.get("area_code", ""))
    result = generate_debug_result(job, company, call["kind"], call["knowledge_snapshot"], pack)

    # Persist the already-generated deterministic conversation one turn at a
    # time.  The structured quote below is intentionally withheld until every
    # turn is visible, so the transcript and offer can never diverge and the
    # realtime UI observes the same lifecycle as a provider-backed call.
    call.update({
        "transcript": [],
        "transcript_kind": "debug_generated_streaming",
        "transcript_streaming": True,
        "transcript_turn_count": 0,
        "last_transcript_at": "",
    })
    db.put("calls", call_id, call, job_id=call["job_id"], company_id=company["id"])
    turns = result.get("transcript", [])
    for turn_index, turn in enumerate(turns, start=1):
        call = db.get("calls", call_id)
        transcript = list(call.get("transcript", []))
        transcript.append(dict(turn))
        call.update({
            "transcript": transcript,
            "transcript_streaming": True,
            "transcript_turn_count": turn_index,
            "last_transcript_at": now(),
        })
        db.put("calls", call_id, call, job_id=call["job_id"], company_id=company["id"])
        if turn_index < len(turns) and call.get("demo_roleplay") \
                and config.DEBUG_TRANSCRIPT_TURN_DELAY_SECS:
            time.sleep(config.DEBUG_TRANSCRIPT_TURN_DELAY_SECS)

    quote_payload = result.get("quote")
    quote = None
    if quote_payload:
        if call["kind"] == "negotiate" and not quote_payload.get("leverage_quote_ids"):
            grounded = result.get("validation", {}).get("grounding", {}).get(
                "used_competing_quotes", [])
            quote_payload["leverage_quote_ids"] = [q.get("id") for q in grounded if q.get("id")]
        if call["kind"] == "negotiate":
            if quote_payload.get("leverage_quote_ids"):
                quote_payload["negotiation_basis"] = "competing_quote"
            elif result.get("validation", {}).get("price_or_terms_changed"):
                quote_payload["negotiation_basis"] = "fee_or_terms"
            else:
                quote_payload["negotiation_basis"] = "standing_offer"
        phase = "initial" if call["kind"] == "quote" else "negotiated"
        model = QuoteIn(job_id=call["job_id"], company_id=company["id"], call_id=call_id,
                        phase=phase, **{k: v for k, v in quote_payload.items()
                                      if k in QuoteIn.model_fields and k not in {
                                          "job_id", "company_id", "call_id", "phase"}})
        quote = model.model_dump()
        allowed_ids = {q["quote_id"] for q in call["knowledge_snapshot"].get(
            "allowed_competitive_claims", [])}
        invalid = set(quote.get("leverage_quote_ids", [])) - allowed_ids
        if invalid:
            raise ValueError(f"debug generator used ungrounded quote ids: {sorted(invalid)}")
        quote["id"] = db.new_id("quote")
        quote["red_flags"] = evaluate_red_flags(model, job["spec"], pack)
        itemized_total = round(sum(item["amount"] for item in quote["line_items"]), 2)
        quote["itemization_delta"] = round(quote["total"] - itemized_total, 2)
        quote["itemization_verified"] = abs(quote["itemization_delta"]) <= 1.0
        quote["created_at"] = now()
        quote["conversation_id"] = ""
        quote["batch_id"] = call["batch_id"]
        quote["knowledge_version"] = call["knowledge_version"]
        from .evidence import verify_quote_counterparty_evidence
        evidence_check = verify_quote_counterparty_evidence(
            {**call, "transcript": result["transcript"]}, quote
        )
        quote["counterparty_evidence"] = evidence_check
        quote["evidence_verified"] = bool(evidence_check.get("valid"))
        quote["grounding_verified"] = bool(
            result.get("validation", {}).get("valid") and evidence_check.get("valid")
        )
        quote["evidence_kind"] = "debug_generated"

    call = db.get("calls", call_id)
    call.update({
        "status": "completed", "ended_at": now(),
        "transcript": result["transcript"], "transcript_kind": "debug_generated",
        "transcript_streaming": False,
        "transcript_turn_count": len(result.get("transcript", [])),
        "last_transcript_at": call.get("last_transcript_at") or now(),
        "debug_generated": True, "audio_path": "", "conversation_id": "",
        **result.get("outcome", {"outcome": "hangup"}),
        "grounding_validation": result.get("validation", {}),
    })
    call.setdefault("outcome", "quote" if quote else "hangup")
    questions = result.get("learned_questions") or questions_from_call(job, call, quote, pack)
    learning = persist_questions(job, questions, source_call_id=call_id, company_id=company["id"])
    call["learning_analysis"] = learning
    db.put("calls", call_id, call, job_id=call["job_id"], company_id=company["id"])
    # Publish the offer only after the associated call is observably terminal.
    # Its fields and evidence still come from the exact streamed `result`.
    if quote:
        db.put("quotes", quote["id"], quote, job_id=call["job_id"],
               company_id=company["id"], phase=quote["phase"])
    if call.get("recall_reservation_id"):
        from .recall_limits import set_status
        set_status(call["job_id"], company["id"], call["recall_reservation_id"],
                   call["status"])


def _run_bridge_group(rows: list[tuple[dict, dict]], batch: dict):
    from elevenlabs.client import ElevenLabs
    from simulation.run_calls import run_bridge_call

    registry = json.loads(config.registry_path().read_text())
    phase = rows[0][0]["kind"]
    our_agent = registry["agents"]["caller" if phase == "quote" else "closer"]
    client = ElevenLabs(api_key=config.ELEVENLABS_API_KEY)
    workers = []
    for call, company in rows:
        if not company.get("agent_id"):
            _fail_call(call["id"], "Simulated company has no counterparty agent_id.")
            continue
        def bridge_one(call=call, company=company):
            try:
                run_bridge_call(client, our_agent, company["agent_id"], call["job_id"],
                                company, phase, False, call_id=call["id"],
                                call_context=call["knowledge_snapshot"])
            except Exception as exc:
                _fail_call(call["id"], f"{type(exc).__name__}: {exc}")
        worker = threading.Thread(target=bridge_one, daemon=True)
        worker.start()
        workers.append(worker)
    for worker in workers:
        worker.join()


def _run_twilio_batch(rows: list[tuple[dict, dict]], batch: dict):
    registry = json.loads(config.registry_path().read_text())
    phase = rows[0][0]["kind"]
    agent_id = registry["agents"]["caller" if phase == "quote" else "closer"]
    recipients = []
    for call, company in rows:
        _set_calling(call["id"])
        recipients.append({
            "id": call["id"], "phone_number": company["phone"],
            "conversation_initiation_client_data": {"dynamic_variables": {
                "job_id": call["job_id"], "company_id": company["id"],
                "company_name": company["name"], "call_id": call["id"],
                "batch_id": call["batch_id"], "phase": phase,
                "knowledge_version": call["knowledge_version"],
                "demo_roleplay": bool(call.get("demo_roleplay")),
            }},
        })
    payload = {
        "call_name": f"QuoteWise {rows[0][0]['job_id']} {phase} batch {batch['index']}",
        "agent_id": agent_id,
        "agent_phone_number_id": config.ELEVENLABS_PHONE_NUMBER_ID,
        "recipients": recipients,
        "target_concurrency_limit": len(recipients),
    }
    headers = {"xi-api-key": config.ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    try:
        response = httpx.post(
            f"{API}/batch-calling/submit", headers=headers, json=payload, timeout=30
        )
    except Exception as exc:
        _lock_uncertain_provider_state(
            rows, batch,
            f"ElevenLabs submission response was not received: {type(exc).__name__}: {exc}",
            reason="provider_submission_unconfirmed",
        )
        raise RuntimeError("ElevenLabs submission is ambiguous; automatic redial locked.") from exc
    if response.status_code >= 500:
        _lock_uncertain_provider_state(
            rows, batch,
            f"ElevenLabs submission returned ambiguous HTTP {response.status_code}.",
            reason="provider_submission_unconfirmed",
        )
        raise RuntimeError(
            f"ElevenLabs submission returned HTTP {response.status_code}; automatic redial locked."
        )
    response.raise_for_status()
    try:
        external_id = str(response.json()["id"])
        if not external_id:
            raise ValueError("empty batch id")
    except Exception as exc:
        _lock_uncertain_provider_state(
            rows, batch,
            "ElevenLabs accepted the request but returned no usable batch id.",
            reason="provider_submission_unconfirmed",
        )
        raise RuntimeError("ElevenLabs batch id is missing; automatic redial locked.") from exc
    stored_batch = db.get("call_batches", batch["id"])
    stored_batch["elevenlabs_batch_id"] = external_id
    db.put("call_batches", batch["id"], stored_batch,
           job_id=batch["job_id"], run_id=batch["run_id"])

    detail = _wait_for_provider_batch(external_id, rows, headers)
    provider_status = str(detail.get("status", "")).lower()
    recipients = detail.get("recipients", []) if isinstance(detail.get("recipients"), list) else []

    from simulation.run_calls import _finalize_call
    succeeded = failed = 0
    for call, company in rows:
        recipient = next((row for row in recipients
                          if _recipient_call(rows, row)
                          and _recipient_call(rows, row)["id"] == call["id"]), None)
        recipient_status = str((recipient or {}).get("status", "missing")).lower()
        conversation_id = str((recipient or {}).get("conversation_id") or "")
        if conversation_id:
            _finalize_call(call["id"], call["job_id"], company["id"], conversation_id)
            succeeded += int((db.get("calls", call["id"]) or {}).get("status") == "completed")
            failed += int((db.get("calls", call["id"]) or {}).get("status") == "failed")
        else:
            reason = ("missing_recipient" if recipient is None else
                      f"provider_recipient_{recipient_status or 'unknown'}")
            _fail_call(
                call["id"],
                f"ElevenLabs batch {provider_status or 'unknown'} ended without a conversation id "
                f"for recipient status {recipient_status}.",
                reason=reason,
                external_status=recipient_status,
            )
            failed += 1

    stored_batch = db.get("call_batches", batch["id"])
    stored_batch.update({
        "provider_status": provider_status,
        "provider_terminal": provider_status in TERMINAL_BATCH_STATUSES,
        "provider_recipient_count": len(recipients),
        "succeeded": succeeded,
        "failed": failed,
    })
    db.put("call_batches", batch["id"], stored_batch,
           job_id=batch["job_id"], run_id=batch["run_id"])


def _wait_for_provider_batch(external_id: str, rows, headers: dict) -> dict:
    """Poll a provider batch to a confirmed terminal state. On local timeout
    cancel remotely and wait a short grace period; never let the next local
    batch start while provider state is still unknown."""
    deadline = time.monotonic() + config.CALL_BATCH_TIMEOUT_SECS
    detail: dict = {}
    poll_errors: list[str] = []
    while time.monotonic() < deadline:
        try:
            detail = _get_provider_batch(external_id, rows, headers)
            if str(detail.get("status", "")).lower() in TERMINAL_BATCH_STATUSES:
                return detail
        except Exception as exc:
            # A single 5xx/network interruption says nothing about provider
            # terminality. Keep polling until the deadline, then cancel.
            poll_errors.append(f"{type(exc).__name__}: {exc}"[:200])
        time.sleep(config.CALL_POLL_INTERVAL_SECS)

    cancel_error = ""
    try:
        response = httpx.post(f"{API}/batch-calling/{external_id}/cancel",
                              headers=headers, timeout=30)
        response.raise_for_status()
    except Exception as exc:
        cancel_error = f"; cancellation failed: {type(exc).__name__}: {exc}"

    grace_deadline = time.monotonic() + min(30.0, max(5.0, config.CALL_POLL_INTERVAL_SECS * 3))
    while time.monotonic() < grace_deadline:
        try:
            detail = _get_provider_batch(external_id, rows, headers)
            if str(detail.get("status", "")).lower() in TERMINAL_BATCH_STATUSES:
                return detail
        except Exception:
            pass
        time.sleep(config.CALL_POLL_INTERVAL_SECS)

    poll_note = f"; last poll error: {poll_errors[-1]}" if poll_errors else ""
    _lock_uncertain_provider_state(
        rows, {"id": rows[0][0].get("batch_id", ""),
               "job_id": rows[0][0].get("job_id", ""),
               "run_id": rows[0][0].get("run_id", "")},
        f"ElevenLabs batch {external_id} timed out and provider terminality "
        f"could not be confirmed{cancel_error}{poll_note}.",
        reason="provider_state_unconfirmed",
        external_id=external_id,
        external_status=str(detail.get("status", "unknown")),
    )
    raise TimeoutError(f"ElevenLabs batch {external_id} terminal state unconfirmed")


def _lock_uncertain_provider_state(rows, batch: dict, error: str, *, reason: str,
                                   external_id: str = "", external_status: str = ""):
    """Terminalize locally but retain a permanent automatic-redial lock.

    An ambiguous external state is materially different from a confirmed
    failure: releasing the local worker must not authorize another phone call.
    """
    for call, _ in rows:
        _fail_call(call["id"], error, reason=reason, external_status=external_status)
        stored = db.get("calls", call["id"]) or call
        stored.update({
            "external_state_uncertain": True,
            "manual_review_required": True,
        })
        if external_id:
            stored["elevenlabs_batch_id"] = external_id
        db.put("calls", stored["id"], stored,
               job_id=stored["job_id"], company_id=stored["company_id"])
    batch_id = batch.get("id", "")
    if batch_id and db.get("call_batches", batch_id):
        stored_batch = db.get("call_batches", batch_id)
        stored_batch.update({
            "provider_terminal": False,
            "provider_state_uncertain": True,
            "manual_review_required": True,
            "provider_status": reason,
        })
        if external_id:
            stored_batch["elevenlabs_batch_id"] = external_id
        db.put("call_batches", batch_id, stored_batch,
               job_id=batch.get("job_id", stored_batch.get("job_id", "")),
               run_id=batch.get("run_id", stored_batch.get("run_id", "")))


def _get_provider_batch(external_id: str, rows, headers: dict) -> dict:
    response = httpx.get(f"{API}/batch-calling/{external_id}", headers=headers, timeout=30)
    response.raise_for_status()
    detail = response.json()
    for recipient in detail.get("recipients", []) if isinstance(detail.get("recipients"), list) else []:
        call = _recipient_call(rows, recipient)
        if not call:
            continue
        stored = db.get("calls", call["id"])
        stored["external_status"] = recipient.get("status", "")
        if recipient.get("conversation_id"):
            stored["conversation_id"] = recipient["conversation_id"]
        db.put("calls", stored["id"], stored,
               job_id=stored["job_id"], company_id=stored["company_id"])
    return detail


def _recipient_call(rows, recipient):
    rid = recipient.get("id", "")
    if rid:
        matched = next((call for call, _ in rows if call["id"] == rid), None)
        if matched:
            return matched
    dynamic = (recipient.get("conversation_initiation_client_data") or {}).get(
        "dynamic_variables", {})
    dynamic_call_id = dynamic.get("call_id", "") if isinstance(dynamic, dict) else ""
    if dynamic_call_id:
        matched = next((call for call, _ in rows if call["id"] == dynamic_call_id), None)
        if matched:
            return matched
    phone = recipient.get("phone_number", "")
    candidates = [call for call, company in rows if company.get("phone") == phone]
    return candidates[0] if len(candidates) == 1 else None


def _fail_call(call_id: str, error: str, *, reason: str = "technical_failure",
               external_status: str = ""):
    call = db.get("calls", call_id)
    if not call or call.get("status") in TERMINAL_CALL_STATUSES:
        return
    call.update({
        "status": "failed", "ended_at": now(),
        "summary": call.get("summary") or "Call did not complete with a usable structured result.",
        "technical_error": error, "error": error,
        "terminal_reason": reason,
        "transcript": call.get("transcript", []),
        "transcript_kind": call.get("transcript_kind") or "none",
        "transcript_streaming": False,
        "transcript_turn_count": len(call.get("transcript", [])),
        "last_transcript_at": call.get("last_transcript_at") or now(),
    })
    if external_status:
        call["external_status"] = external_status
    if not call.get("outcome"):
        call["outcome"] = "hangup"
        call["outcome_inferred"] = True
    try:
        from .learnings import persist_questions, questions_from_call
        job = db.get("jobs", call["job_id"])
        call["learning_analysis"] = persist_questions(
            job, questions_from_call(job, call), source_call_id=call_id,
            company_id=call["company_id"])
    except Exception as exc:
        call["learning_analysis"] = {"logged": False, "error": str(exc)[:200]}
    db.put("calls", call_id, call, job_id=call["job_id"], company_id=call["company_id"])
    if call.get("recall_reservation_id"):
        try:
            from .recall_limits import set_status
            set_status(call["job_id"], call["company_id"], call["recall_reservation_id"],
                       "failed")
        except Exception:
            pass


def _fail_unfinished(job_id: str, run_id: str, error: str):
    for call in db.where("calls", job_id=job_id):
        if call.get("run_id") == run_id and call.get("status") not in TERMINAL_CALL_STATUSES:
            _fail_call(call["id"], error)


def start_demo_call(job_id: str, company_id: str, phase: str) -> dict:
    """Explicitly call the configured, allow-listed human demo phone while
    preserving the selected real Google vendor's identity and history."""
    if not config.DEMO_PHONE_NUMBER:
        raise RuntimeError("DEMO_PHONE_NUMBER missing — configure the authorised demo phone in .env.")
    if not config.ELEVENLABS_PHONE_NUMBER_ID:
        raise RuntimeError("ELEVENLABS_PHONE_NUMBER_ID missing — import the Twilio number in ElevenLabs.")
    if not config.ELEVENLABS_API_KEY or not config.registry_path().exists():
        raise RuntimeError("ElevenLabs is not configured/provisioned for the live demo.")
    job = db.get("jobs", job_id)
    if not job:
        raise LookupError("job not found")
    if job.get("archived"):
        raise RuntimeError("Archived jobs are read-only. Reset the demo to create a fresh job.")
    demo = _demo_mode(job)
    company = db.get("companies", company_id)
    job_company_ids = {c["id"] for c in db.where("companies", job_id=job_id)}
    if not company or company_id not in job_company_ids:
        raise LookupError("company not found on this job")
    if company.get("source") != "google_places" or not company.get("external_ids", {}).get(
            "google_places"):
        raise LookupError("Live demo requires a real Google Places vendor selected from this job.")
    if demo:
        if company_id != demo.get("live_company_id"):
            raise LookupError("This demo session can route only its preselected live role-play vendor.")
        if phase == "quote":
            raise RuntimeError(
                "The selected human quote must be gathered as part of the all-vendor batch. "
                "Reset the demo if that live batch attempt needs to be repeated."
            )
        state = queue_state(job_id)
        if state["running"] or state["summary"]["called"] != state["summary"]["total"]:
            raise RuntimeError(
                "Finish the complete all-vendor quote run before starting the live negotiation."
            )
    if phase == "negotiate" and not any(
            q["company_id"] == company_id and q.get("evidence_verified")
            and q.get("grounding_verified")
            and q.get("itemization_verified") is True
            and q.get("evidence_kind") != "debug_generated"
            for q in db.where("quotes", job_id=job_id)):
        raise LookupError("This vendor has no verified quote to negotiate yet.")

    _validate_agent_registry(job)
    prior_calls = db.where("calls", job_id=job_id, company_id=company_id)
    if any(call.get("status") not in TERMINAL_CALL_STATUSES for call in prior_calls):
        raise RuntimeError("A call to this vendor is already queued or in progress; wait for it to finish.")
    call_id = db.new_id("call")
    run_id = f"demo:{call_id}"
    from .runclaims import claim_run, finish_run
    lease_seconds = max(config.CALL_RUN_LEASE_SECS, config.CALL_BATCH_TIMEOUT_SECS + 120)
    claim = claim_run(job_id, run_id=run_id, stale_after=lease_seconds,
                      metadata={"mode": "demo_phone", "phase": phase,
                                "company_id": company_id})
    if not claim.acquired:
        raise RuntimeError("Another call run or live demo is already active for this job.")
    try:
        version = int(job.get("knowledge_version", 0))
        # Normal live calls must never spend synthetic debug evidence. The
        # sole exception is this explicitly prepared, disclosed human
        # role-play, where every synthetic amount is labelled as such aloud.
        snapshot = create_snapshot(
            job_id, version,
            # Only the server-side allow-listed human role-play may spend
            # synthetic demo-market evidence, and its context/prompt must
            # disclose that evidence as simulated. Normal live calls remain
            # unable to cite debug data.
            allow_debug_leverage=bool(demo),
        )
        context = context_for(snapshot, company_id)
        if demo and phase == "negotiate" and not context.get("allowed_competitive_claims"):
            raise LookupError(
                "No grounded competing demo-market offer exists yet; negotiation is blocked."
            )
        recall = None
        if phase == "negotiate" or prior_calls:
            from .recall_limits import reserve as reserve_recall
            reservation_id = f"demo:{call_id}"
            slot = reserve_recall(
                job_id, company_id, reservation_id,
                max_recalls=config.MAX_VENDOR_RECALLS,
                status="reserved", metadata={"mode": "demo_phone", "phase": phase},
            )
            if slot is None:
                raise LookupError(
                    f"This vendor reached the hard limit of {config.MAX_VENDOR_RECALLS} recalls."
                )
            recall = {"reservation_id": reservation_id, "slot": slot}
        call = {
            "id": call_id, "job_id": job_id, "company_id": company_id,
            "kind": phase, "mode": "demo_phone", "status": "queued",
            "knowledge_version": version, "knowledge_snapshot": context,
            "spec_hash": snapshot["spec_hash"], "attempt_number": len(db.where(
                "calls", job_id=job_id, company_id=company_id)) + 1,
            "created_at": now(), "run_id": run_id,
            "dialed_to": "configured_demo_phone",
        }
        if demo:
            call.update({
                "demo_roleplay": True,
                "demo_session_id": demo.get("session_id", ""),
                "counterparty_setup": "human_roleplay",
            })
        if recall:
            call["recall_reservation_id"] = recall["reservation_id"]
            call["recall_slot"] = recall["slot"]
        batch_id = db.new_id("batch")
        call["batch_id"] = batch_id
        batch = {
            "id": batch_id, "run_id": run_id, "job_id": job_id,
            "index": 1, "status": "queued", "company_ids": [company_id],
            "knowledge_version": version, "knowledge_snapshot": snapshot,
            "completed": 0, "created_at": now(), "demo": True,
        }
        db.put("calls", call_id, call, job_id=job_id, company_id=company_id)
        if recall:
            from .recall_limits import attach_call
            if not attach_call(job_id, company_id, recall["reservation_id"], call_id,
                               status="queued"):
                raise RuntimeError("Recall reservation disappeared before the demo call was queued.")
        db.put("call_batches", batch_id, batch, job_id=job_id, run_id=batch["run_id"])
        # A native one-recipient batch has reliable recipient tracking and a
        # cancellation endpoint, unlike the outbound endpoint's nullable
        # conversation_id. The selected vendor identity stays unchanged in DB;
        # only this in-memory destination is the authorised human demo number.
        destination = {**company, "phone": config.DEMO_PHONE_NUMBER}
        thread = threading.Thread(
            target=_run_demo_batch,
            args=(call, destination, batch, claim.owner_token, lease_seconds), daemon=True,
            name=f"quotewise-demo-{call_id}",
        )
        thread.start()
    except Exception:
        finish_run(job_id, run_id, claim.owner_token, "failed")
        if db.get("calls", call_id):
            _fail_call(call_id, "Demo setup failed before provider submission.",
                       reason="demo_setup_failed")
        raise
    return {
        "dialing": True, "call_id": call_id, "company_id": company_id,
        "company_name": company["name"], "batch_id": batch_id,
        "to_number": _mask_phone(config.DEMO_PHONE_NUMBER),
    }


def _run_demo_batch(call: dict, destination: dict, batch: dict,
                    owner_token: str, lease_seconds: float):
    from .runclaims import finish_run, heartbeat_run
    batch_row = db.get("call_batches", batch["id"])
    batch_row.update({"status": "running", "started_at": now()})
    db.put("call_batches", batch["id"], batch_row,
           job_id=batch["job_id"], run_id=batch["run_id"])
    try:
        if not heartbeat_run(call["job_id"], batch["run_id"], owner_token,
                             stale_after=lease_seconds):
            raise RuntimeError("Demo run ownership was lost before provider submission.")
        _run_twilio_batch([(call, destination)], batch_row)
    except Exception as exc:
        _fail_call(call["id"], f"{type(exc).__name__}: {exc}", reason="demo_batch_error")
    finally:
        claim_status = "failed"
        try:
            terminal = db.get("calls", call["id"]) or {}
            batch_row = db.get("call_batches", batch["id"]) or batch
            batch_row.update({
                "completed": int(terminal.get("status") in TERMINAL_CALL_STATUSES),
                "succeeded": int(terminal.get("status") == "completed"),
                "failed": int(terminal.get("status") == "failed"),
                "status": "completed" if terminal.get("status") == "completed" else "failed",
                "ended_at": now(),
            })
            db.put("call_batches", batch["id"], batch_row,
                   job_id=batch["job_id"], run_id=batch["run_id"])
            _advance_knowledge(call["job_id"])
            claim_status = ("completed" if terminal.get("status") == "completed"
                            else "failed")
        finally:
            # Bookkeeping failures must not leak the job-wide lease and block
            # every future batch/demo indefinitely.
            finish_run(call["job_id"], batch["run_id"], owner_token, claim_status)


def _mask_phone(value: str) -> str:
    return f"•••{value[-4:]}" if len(value) >= 4 else "configured"
