"""Offline safety test for the non-destructive demo reset command.

The database, recordings directory and run/recall ledgers live in one temporary
directory. Network and telephony boundaries are replaced with functions that
fail immediately, so this test cannot initiate a call.

Run with: ``.venv/bin/python -m tests.demo_reset_test``
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile


# Configuration is loaded at import time. Explicit values take precedence over
# the developer's .env and keep every artifact inside a throwaway directory.
os.environ["NEGOTIATOR_DATA_DIR"] = tempfile.mkdtemp(prefix="demo-reset-test-")
os.environ["DEBUG_CALLS"] = "true"
os.environ["LIVE_VENDOR_CALLS_ENABLED"] = "false"
os.environ["DEMO_PHONE_NUMBER"] = "+16505550199"
os.environ["ELEVENLABS_PHONE_NUMBER_ID"] = "phone_test_never_submitted"
os.environ["ELEVENLABS_API_KEY"] = "test_key_never_submitted"
os.environ["VERTICAL"] = "plumbing"
os.environ.setdefault("AGENT_TOOL_SECRET", "offline-test-tool-secret")

from negotiator import callrunner, config, db, demo_reset
from negotiator.recall_limits import for_company, reserve
from negotiator.runclaims import RunClaimStore, claim_run, finish_run
from negotiator.seed import demo_user


CALL_LIST = {
    "complete": True,
    "saved": True,
    "items": [
        {
            "name": "Zulu Plumbing",
            "phone": "+17045550103",
            "address": "3 Trade Street, Charlotte, NC",
            "sources": ["google_places"],
            "source_ids": {"google_places": "places/zulu"},
            "rating": 4.7,
            "review_count": 103,
        },
        {
            "name": "Alpha Plumbing",
            "phone": "+17045550101",
            "address": "1 Trade Street, Charlotte, NC",
            "sources": ["google_places"],
            "source_ids": {"google_places": "places/alpha"},
            "rating": 4.9,
            "review_count": 101,
        },
        {
            "name": "Beta Plumbing",
            "phone": "+17045550102",
            "address": "2 Trade Street, Charlotte, NC",
            "sources": ["google_places"],
            "source_ids": {"google_places": "places/beta"},
            "rating": 4.8,
            "review_count": 102,
        },
    ],
}


def _seed_saved_market() -> str:
    user = demo_user()
    job_id = "job_saved_market"
    db.put("jobs", job_id, {
        "id": job_id,
        "vertical": "plumbing",
        "area_code": "28202",
        "user_id": user["id"],
        "spec": {},
        "spec_source": "form",
        "confirmed": False,
        "call_list": CALL_LIST,
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    return job_id


def _attach_terminal_evidence(job_id: str) -> dict:
    company = next(
        row for row in db.where("companies", job_id=job_id)
        if row["name"] == "Alpha Plumbing"
    )
    call_id = "call_reset_evidence"
    quote_id = "quote_reset_evidence"
    run_id = "run_reset_evidence"
    batch_id = "batch_reset_evidence"
    recording = config.RECORDINGS_DIR / "reset-evidence.mp3"
    recording.write_bytes(b"offline evidence fixture")

    db.put("calls", call_id, {
        "id": call_id,
        "job_id": job_id,
        "company_id": company["id"],
        "kind": "quote",
        "status": "completed",
        "outcome": "quote",
        "transcript": [
            {"role": "agent", "text": "What is your complete itemised total?"},
            {"role": "vendor", "text": "Our binding itemised total is $2,400."},
        ],
        "transcript_kind": "offline_evidence_fixture",
        "audio_path": str(recording),
        "conversation_id": "conv_reset_evidence",
        "run_id": run_id,
        "batch_id": batch_id,
        "created_at": "2026-01-02T00:00:00+00:00",
        "started_at": "2026-01-02T00:00:01+00:00",
        "ended_at": "2026-01-02T00:01:00+00:00",
        "grounding_validation": {"valid": True, "offline_fixture": True},
    }, job_id=job_id, company_id=company["id"])
    db.put("quotes", quote_id, {
        "id": quote_id,
        "job_id": job_id,
        "company_id": company["id"],
        "call_id": call_id,
        "conversation_id": "conv_reset_evidence",
        "phase": "initial",
        "line_items": [{
            "label": "All-in service",
            "code": "base",
            "amount": 2400.0,
            "kind": "base",
        }],
        "total": 2400.0,
        "binding": True,
        "deposit": 0.0,
        "conditions": [],
        "verbatim_evidence": "Our binding itemised total is $2,400.",
        "evidence_verified": True,
        "grounding_verified": True,
        "itemization_verified": True,
        "evidence_kind": "voice_transcript",
        "created_at": "2026-01-02T00:01:00+00:00",
    }, job_id=job_id, company_id=company["id"], phase="initial")
    db.put("call_runs", run_id, {
        "id": run_id,
        "job_id": job_id,
        "phase": "quote",
        "status": "completed",
        "company_ids": [company["id"]],
        "created_at": "2026-01-02T00:00:00+00:00",
        "ended_at": "2026-01-02T00:01:00+00:00",
    }, job_id=job_id)
    db.put("call_batches", batch_id, {
        "id": batch_id,
        "job_id": job_id,
        "run_id": run_id,
        "index": 1,
        "status": "completed",
        "company_ids": [company["id"]],
        "completed": 1,
        "created_at": "2026-01-02T00:00:00+00:00",
        "ended_at": "2026-01-02T00:01:00+00:00",
    }, job_id=job_id, run_id=run_id)

    claim = claim_run(job_id, run_id=run_id, stale_after=60)
    assert claim.acquired and claim.owner_token
    assert finish_run(job_id, run_id, claim.owner_token, "completed")
    slot = reserve(
        job_id,
        company["id"],
        "reset-evidence-recall",
        call_id=call_id,
        status="completed",
        metadata={"offline_fixture": True},
    )
    assert slot == 1
    return {
        "company_id": company["id"],
        "call_id": call_id,
        "quote_id": quote_id,
        "run_id": run_id,
        "batch_id": batch_id,
        "recording": recording,
    }


def main() -> None:
    _seed_saved_market()
    external_attempts: list[str] = []

    def forbidden_external(*args, **kwargs):
        external_attempts.append(str(args[0] if args else "telephony"))
        raise AssertionError("demo reset crossed a network/telephony boundary")

    original_discover = demo_reset._discover_call_list
    original_batch = callrunner._run_twilio_batch
    original_post, original_get = callrunner.httpx.post, callrunner.httpx.get
    demo_reset._discover_call_list = forbidden_external
    callrunner._run_twilio_batch = forbidden_external
    callrunner.httpx.post = forbidden_external
    callrunner.httpx.get = forbidden_external
    try:
        # With no query the choice is stable and independent of random company ids.
        first = demo_reset.reset()
        assert first["discovery_deferred"] is True
        assert first["live_company_id"] == ""
        first_job = db.get("jobs", first["job_id"])
        assert first_job["confirmed"] is False and first_job["archived"] is False
        assert first_job["spec"] == {}
        assert "call_list" not in first_job
        assert db.where("companies", job_id=first["job_id"]) == []
        # Attach historical evidence only to prove the next reset preserves it;
        # launch-time discovery itself is covered by demo_launch_test.
        first_job["call_list"] = CALL_LIST
        db.put("jobs", first["job_id"], first_job)
        callrunner.sync_google_companies(first["job_id"])
        evidence = _attach_terminal_evidence(first["job_id"])

        # The second reset selects a different real Google identity by query and
        # archives, rather than deletes, the completed first demo.
        second = demo_reset.reset(live_vendor="Zulu")
        assert second["archived_previous"] == [first["job_id"]]
        archived = db.get("jobs", first["job_id"])
        assert archived["archived"] is True
        assert archived["superseded_by_job_id"] == second["job_id"]
        assert archived["demo_mode"]["active"] is False
        try:
            callrunner.start_calls(first["job_id"], "quote")
        except RuntimeError as exc:
            assert "read-only" in str(exc)
        else:
            raise AssertionError("an archived demo job was allowed to schedule calls")

        # Calls, quotes, run/batch audit rows, claims, recall ledger and the
        # recording all survive the reset byte-for-byte.
        assert db.get("calls", evidence["call_id"])["grounding_validation"]["valid"] is True
        assert db.get("quotes", evidence["quote_id"])["evidence_verified"] is True
        assert db.get("call_runs", evidence["run_id"])["status"] == "completed"
        assert db.get("call_batches", evidence["batch_id"])["status"] == "completed"
        claim_state = RunClaimStore().get(evidence["run_id"])
        assert claim_state and claim_state.status == "completed"
        assert len(for_company(first["job_id"], evidence["company_id"])) == 1
        assert Path(evidence["recording"]).read_bytes() == b"offline evidence fixture"

        fresh = db.get("jobs", second["job_id"])
        assert fresh["confirmed"] is False
        assert fresh["spec"] == {}
        assert fresh["spec_source"] == "demo"
        assert fresh["knowledge_version"] == 0 and fresh["follow_up_plan"] == []
        assert fresh["demo_mode"]["roleplay"] is True
        assert fresh["demo_mode"]["auto_negotiate"] is True
        assert fresh["demo_mode"]["live_company_id"] == ""
        assert fresh["demo_mode"]["live_company_name"].startswith("Pending")
        assert fresh["demo_mode"]["template"]["source_job_id"] == ""
        assert fresh["demo_mode"]["selection_query"] == "Zulu"
        assert fresh["demo_mode"]["discovery"]["required_at_launch"] is True
        assert db.where("calls", job_id=second["job_id"]) == []
        assert db.where("quotes", job_id=second["job_id"]) == []
        assert db.where("call_runs", job_id=second["job_id"]) == []
        assert db.where("call_batches", job_id=second["job_id"]) == []

        assert db.where("companies", job_id=second["job_id"]) == []
        assert fresh["demo_mode"]["live_company_google_place_id"] == ""

        # An active attempt blocks reset before a third job can be created or
        # the current demo can be archived.
        active_call_id = "call_active_reset_guard"
        db.put("calls", active_call_id, {
            "id": active_call_id,
            "job_id": second["job_id"],
            "company_id": "co_pending_launch",
            "kind": "quote",
            "status": "queued",
            "created_at": "2026-01-03T00:00:00+00:00",
        }, job_id=second["job_id"], company_id="co_pending_launch")
        jobs_before = {job["id"] for job in db.where("jobs")}
        try:
            demo_reset.reset()
        except RuntimeError as exc:
            assert "active or leased calls" in str(exc)
        else:
            raise AssertionError("reset accepted a demo with active work")
        assert {job["id"] for job in db.where("jobs")} == jobs_before
        assert db.get("jobs", second["job_id"])["archived"] is False
        assert db.get("calls", active_call_id)["status"] == "queued"
        assert not external_attempts

        print(
            "DEMO RESET TEST PASSED: archive preserved evidence, clean job, "
            "deferred live discovery, no calls, active-work guard"
        )
    finally:
        demo_reset._discover_call_list = original_discover
        callrunner._run_twilio_batch = original_batch
        callrunner.httpx.post, callrunner.httpx.get = original_post, original_get


if __name__ == "__main__":
    main()
