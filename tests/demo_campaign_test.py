"""Offline integration test for the resettable hybrid demo campaign.

The campaign keeps nine discovered Google vendors in transcript-only debug
mode and routes one real Google vendor identity to the configured role-player
phone. The exploratory role-player call is the first member of quote batch 1.
After all three sqrt(n) quote barriers publish their knowledge, the same run
automatically appends batch 4 and calls the role-player back with the exact
final competitive snapshot.

No HTTP request, ElevenLabs conversation, telephone call, or audio artifact is
created by this test.  ``_run_twilio_batch`` is replaced at the provider
boundary with a deterministic terminal result.

Run with: ``.venv/bin/python -m tests.demo_campaign_test``
"""
from __future__ import annotations

from copy import deepcopy
import math
import os
import tempfile
import time
import uuid


# Config is read at import time.  Explicit values take precedence over a local
# developer .env and guarantee that this test can never dial a real number.
os.environ["NEGOTIATOR_DATA_DIR"] = tempfile.mkdtemp(prefix="demo-campaign-test-")
os.environ["DEBUG_CALLS"] = "true"
os.environ["LIVE_VENDOR_CALLS_ENABLED"] = "false"
os.environ["DEMO_PHONE_NUMBER"] = "+16505550199"
os.environ["ELEVENLABS_PHONE_NUMBER_ID"] = "phone_test_never_submitted"
os.environ["ELEVENLABS_API_KEY"] = "test_key_never_submitted"
os.environ["VERTICAL"] = "plumbing"
os.environ["DEBUG_TRANSCRIPT_TURN_DELAY_SECS"] = "0"
os.environ.setdefault("AGENT_TOOL_SECRET", "offline-test-tool-secret")

from fastapi.testclient import TestClient

from negotiator import callrunner, config, db
from negotiator.recall_limits import for_company as recall_reservations_for_company
from negotiator.server import app


SPEC = {
    "area_code": "28202",
    "job_type": "water_heater",
    "problem_description": "40 gallon tank is leaking at the base",
    "property_type": "house",
    "urgency": "this_week",
    "access": {
        "floor": 1,
        "crawlspace": False,
        "slab_foundation": False,
        "tight_access": False,
    },
}


def _wait_queue(client: TestClient, job_id: str, headers: dict, timeout: float = 10) -> dict:
    deadline = time.time() + timeout
    state = None
    while time.time() < deadline:
        state = client.get(f"/api/jobs/{job_id}/call-queue", headers=headers).json()
        if not state["running"]:
            return state
        time.sleep(0.02)
    raise AssertionError(f"campaign did not finish: {state}")


def _wait_call(call_id: str, timeout: float = 5) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        call = db.get("calls", call_id)
        if call and call.get("ended_at"):
            return call
        time.sleep(0.01)
    raise AssertionError(f"call {call_id} did not become terminal")


def _store_provider_quote(call: dict, company: dict) -> None:
    """Produce a provider-shaped, evidence-verified result without voice/audio."""
    stored = db.get("calls", call["id"])
    context = stored["knowledge_snapshot"]
    phase = "initial" if stored["kind"] == "quote" else "negotiated"
    leverage_ids: list[str] = []
    negotiation_basis = "none"

    if phase == "initial":
        total = 1848.0
        vendor_sentence = (
            "For that exact scope, our itemised labor-and-parts package is "
            "a binding total of $1,848."
        )
        line_items = [
            {"label": "Labor and parts package", "code": "base", "amount": 1848.0,
             "kind": "base", "contingent": False, "notes": ""},
        ]
        agent_turns = [
            {
                "role": "agent",
                "text": (
                    "Hello, I am an AI calling on behalf of a customer in a recorded demo "
                    "role-play. I am exploring your quote, not negotiating today."
                ),
            },
            {
                "role": "agent",
                "text": (
                    "For the confirmed water-heater scope, what work is included, what is "
                    "the complete itemised price, and which conditions could change it?"
                ),
            },
        ]
    else:
        claims = context.get("allowed_competitive_claims", [])
        assert claims, "the role-play closer received no frozen competitive claims"
        leverage = min(claims, key=lambda row: row["total"])
        leverage_ids = [leverage["quote_id"]]
        negotiation_basis = "competing_quote"
        # The human role-player's planned second response is the campaign win:
        # it beats the best grounded simulated offer and becomes the UI best.
        total = round(max(1.0, float(leverage["total"]) - 100.0), 2)
        assert total < 3400.0
        agent_turns = [{"role": "agent", "text": (
            "For clarity, this is a recorded demo role-play. "
            f"I have a simulated demo-market offer labelled {leverage['company']} "
            f"at ${float(leverage['total']):,.2f}; can you beat it?"
        )}]
        vendor_sentence = f"Using that competing offer, I can make our binding total ${total:,.2f}."
        line_items = [
            {"label": "Negotiated all-in total", "code": "base", "amount": total,
             "kind": "base", "contingent": False, "notes": ""},
        ]

    quote_id = db.new_id("quote")
    quote = {
        "id": quote_id,
        "job_id": stored["job_id"],
        "company_id": company["id"],
        "call_id": stored["id"],
        "conversation_id": f"offline_{stored['id']}",
        "batch_id": stored["batch_id"],
        "knowledge_version": stored["knowledge_version"],
        "line_items": line_items,
        "total": total,
        "binding": True,
        "deposit": 0.0,
        "valid_until": "",
        "conditions": ["Same confirmed scope"],
        "verbatim_evidence": vendor_sentence,
        "phase": phase,
        "leverage_quote_ids": leverage_ids,
        "negotiation_basis": negotiation_basis,
        "red_flags": [],
        "itemization_delta": 0.0,
        "itemization_verified": True,
        "evidence_verified": True,
        "grounding_verified": True,
        "evidence_kind": "demo_roleplay_voice",
        "created_at": callrunner.now(),
    }
    db.put("quotes", quote_id, quote, job_id=stored["job_id"],
           company_id=company["id"], phase=phase)

    stored.update({
        "status": "completed",
        "outcome": "quote",
        "summary": "Offline provider fixture captured an itemised quote.",
        "conversation_id": quote["conversation_id"],
        "transcript": [
            *agent_turns,
            {"role": "vendor", "text": vendor_sentence},
        ],
        "transcript_kind": "offline_voice_fixture",
        "audio_path": "",
        "learning_analysis": {"logged": True, "added": [], "updated": [],
                              "already_known": []},
        "ended_at": callrunner.now(),
    })
    from negotiator.evidence import validate_call_grounding
    grounding = validate_call_grounding(stored, [quote])
    assert grounding["valid"], grounding
    stored["grounding_validation"] = {**grounding, "offline_fixture": True}
    db.put("calls", stored["id"], stored, job_id=stored["job_id"],
           company_id=company["id"])
    if stored.get("recall_reservation_id"):
        from negotiator.recall_limits import set_status
        set_status(stored["job_id"], company["id"],
                   stored["recall_reservation_id"], "completed")


def main() -> None:
    assert config.DEBUG_CALLS is True
    assert config.DEMO_PHONE_NUMBER == "+16505550199"

    client = TestClient(app)
    auth = client.post("/api/auth/register", json={
        "email": f"demo-campaign-{uuid.uuid4().hex[:8]}@test.dev",
        "password": "secret123",
    })
    auth.raise_for_status()
    headers = {"Authorization": f"Bearer {auth.json()['token']}"}
    job = client.post("/api/jobs", json={"vertical": "plumbing"}, headers=headers).json()
    client.put(f"/api/jobs/{job['id']}/spec", json={"spec": SPEC},
               headers=headers).raise_for_status()
    client.post(f"/api/jobs/{job['id']}/confirm", headers=headers).raise_for_status()

    stored_job = db.get("jobs", job["id"])
    stored_job["call_list"] = {
        "complete": True,
        "saved": True,
        "items": [
            {
                "name": f"Real Google Plumbing Vendor {index + 1}",
                "phone": f"+17045550{100 + index}",
                "sources": ["google_places"],
                "source_ids": {"google_places": f"places/demo-{index + 1}"},
                "rating": 4.9 - index / 20,
                "review_count": 100 + index,
            }
            for index in range(10)
        ],
    }
    db.put("jobs", job["id"], stored_job)
    companies = callrunner.sync_google_companies(job["id"])
    assert len(companies) == 10
    target = companies[4]
    target_before = deepcopy(db.get("companies", target["id"]))

    stored_job = db.get("jobs", job["id"])
    stored_job["demo_mode"] = {
        "active": True,
        "roleplay": True,
        "live_company_id": target["id"],
        "live_company_name": target["name"],
        "session_id": f"demo-session-{uuid.uuid4().hex[:8]}",
        "auto_negotiate": True,
    }
    stored_job["documents"] = [{"id": "doc_demo", "filename": "demo.pdf"}]
    stored_job["spec_source"] = "document+interview"
    db.put("jobs", job["id"], stored_job)

    submissions: list[dict] = []
    network_attempts: list[str] = []
    original_twilio = callrunner._run_twilio_batch
    original_post, original_get = callrunner.httpx.post, callrunner.httpx.get

    def forbidden_network(*args, **kwargs):
        network_attempts.append(str(args[0] if args else "unknown"))
        raise AssertionError("offline demo test attempted network access")

    def fake_twilio(rows: list[tuple[dict, dict]], batch: dict):
        # In hybrid debug mode only the allow-listed role-player may cross the
        # telephony boundary.  All nine other vendors stay transcript-only.
        assert rows and {company["id"] for _, company in rows} == {target["id"]}
        submissions.append({
            "batch_id": batch["id"],
            "batch_index": batch["index"],
            "rows": [(call["id"], company["id"], company["phone"])
                     for call, company in rows],
        })
        for call, destination in rows:
            assert call.get("demo_roleplay") is True
            assert destination["phone"] == config.DEMO_PHONE_NUMBER
            callrunner._set_calling(call["id"])
            _store_provider_quote(call, destination)

    try:
        callrunner._run_twilio_batch = fake_twilio
        callrunner.httpx.post = forbidden_network
        callrunner.httpx.get = forbidden_network

        denied = client.post(f"/api/jobs/{job['id']}/calls/start", json={
            "phase": "quote", "idempotency_key": f"unapproved-{uuid.uuid4().hex}",
        }, headers=headers)
        assert denied.status_code == 409
        assert "authorize_demo_calls=true" in denied.text
        assert submissions == [] and db.where("calls", job_id=job["id"]) == []

        response = client.post(f"/api/jobs/{job['id']}/calls/start", json={
            "phase": "quote",
            "authorize_demo_calls": True,
            "idempotency_key": f"demo-quote-{uuid.uuid4().hex}",
        }, headers=headers)
        response.raise_for_status()
        started = response.json()
        assert started["batch_size"] == math.ceil(math.sqrt(10)) == 4
        assert started["quote_batch_count"] == 3
        assert started["batch_count"] == 4 and started["total"] == 10
        assert started["total_calls"] == 11
        assert started["auto_negotiation_batch"] == 4
        assert started["demo_calls_authorized"] is True

        state = _wait_queue(client, job["id"], headers)
        calls = db.where("calls", job_id=job["id"])
        initial_calls = [call for call in calls if call["kind"] == "quote"]
        negotiation_calls = [call for call in calls if call["kind"] == "negotiate"]
        batches = sorted((batch for batch in db.where("call_batches", job_id=job["id"])
                          if batch.get("run_id") == started["run_id"]),
                         key=lambda row: row["index"])
        assert [len(batch["company_ids"]) for batch in batches] == [4, 4, 2, 1]
        assert [batch["knowledge_version"] for batch in batches] == [0, 1, 2, 3]
        assert [batch.get("phase") for batch in batches] == [
            "quote", "quote", "quote", "negotiate"
        ]
        assert batches[-1]["auto_negotiation"] is True
        assert len(initial_calls) == 10
        assert len(negotiation_calls) == 1
        assert sum(call["mode"] == "debug_transcript" for call in initial_calls) == 9
        target_initial = next(call for call in initial_calls
                              if call["company_id"] == target["id"])
        assert target_initial["mode"] == "demo_phone"
        assert target_initial["batch_index"] == 1
        assert target_initial.get("demo_roleplay") is True
        assert target_initial["knowledge_snapshot"].get("demo_roleplay") is True
        assert target_initial["knowledge_snapshot"]["competing_quotes"] == []
        initial_dialogue = " ".join(
            turn["text"] for turn in target_initial.get("transcript", [])
        ).casefold()
        assert "ai" in initial_dialogue and "recorded demo role-play" in initial_dialogue
        assert "explor" in initial_dialogue
        assert not any(token in initial_dialogue for token in (
            "competitor", "price-match", "price match", "can you beat", "concession"
        ))

        # Only the in-memory provider destination changed.  The saved Google
        # identity and its real dial target remain byte-for-byte unchanged.
        assert db.get("companies", target["id"]) == target_before
        auto_call = negotiation_calls[0]
        assert submissions == [
            {
                "batch_id": target_initial["batch_id"],
                "batch_index": 1,
                "rows": [(target_initial["id"], target["id"],
                          config.DEMO_PHONE_NUMBER)],
            },
            {
                "batch_id": auto_call["batch_id"],
                "batch_index": 4,
                "rows": [(auto_call["id"], target["id"], config.DEMO_PHONE_NUMBER)],
            },
        ]
        assert target_before["phone"] != config.DEMO_PHONE_NUMBER
        assert target_before["external_ids"]["google_places"] == "places/demo-5"
        assert state["summary"]["called"] == state["summary"]["total"] == 10

        quotes = db.where("quotes", job_id=job["id"])
        assert len(quotes) == 11
        initial_quotes = [quote for quote in quotes if quote["phase"] == "initial"]
        assert len(initial_quotes) == 10
        target_quote = next(q for q in initial_quotes if q["company_id"] == target["id"])
        assert target_quote["evidence_kind"] == "demo_roleplay_voice"
        assert target_quote["evidence_verified"] and target_quote["grounding_verified"]
        assert target_quote["itemization_verified"] is True
        assert not target_initial.get("audio_path")

        # The callback was appended automatically inside the same durable run
        # only after all ten initial calls reached terminal state.
        assert auto_call.get("demo_roleplay") is True
        assert auto_call.get("auto_negotiation") is True
        assert auto_call["run_id"] == started["run_id"]
        context = auto_call["knowledge_snapshot"]
        assert context.get("demo_roleplay") is True
        rules = context.get("rules", "").casefold()
        assert "role" in rules and ("synthetic" in rules or "debug" in rules), rules
        own_ids = {row["quote_id"] for row in context["own_quote_history"]}
        assert target_quote["id"] in own_ids
        allowed = context["allowed_competitive_claims"]
        debug_quotes = [q for q in quotes if q["evidence_kind"] == "debug_generated"]
        assert len(allowed) == len(debug_quotes) == 9
        by_id = {q["id"]: q for q in debug_quotes}
        for claim in allowed:
            source = by_id[claim["quote_id"]]
            source_company = db.get("companies", source["company_id"])
            assert claim["company"] == source_company["name"]
            assert claim["total"] == source["total"]

        negotiated = [q for q in db.where("quotes", job_id=job["id"],
                                           company_id=target["id"])
                      if q["phase"] == "negotiated"]
        assert len(negotiated) == 1
        asserted_claim_ids = {row["quote_id"] for row in allowed}
        assert set(negotiated[0]["leverage_quote_ids"]) <= asserted_claim_ids
        assert negotiated[0]["total"] < target_quote["total"]
        assert "simulated demo-market offer" in " ".join(
            turn["text"] for turn in auto_call["transcript"] if turn["role"] == "agent"
        ).casefold()
        assert auto_call["grounding_validation"]["valid"] is True
        assert len(recall_reservations_for_company(job["id"], target["id"])) == 1
        reservations = recall_reservations_for_company(job["id"], target["id"])
        assert [reservation.slot for reservation in reservations] == [1]
        assert reservations[0].status == "completed"

        target_calls = [call for call in calls if call["company_id"] == target["id"]]
        assert len(target_calls) == 2
        negotiated_quote = next(q for q in quotes if q["phase"] == "negotiated")
        assert negotiated_quote["total"] < min(
            quote["total"] for quote in initial_quotes if quote["company_id"] != target["id"]
        )
        assert state["summary"]["current_best_offer"]["company_id"] == target["id"]
        assert state["summary"]["current_best_offer"]["total"] == negotiated_quote["total"]
        assert state["batch"]["quote_batch_count"] == 3
        assert state["batch"]["auto_negotiation_batch"] == 4
        assert state["batch"]["auto_negotiation_status"] == "completed"

        # Every synthetic call exposes the progressive transcript fields and
        # publishes its quote only after the terminal transition.
        synthetic_calls = [call for call in initial_calls if call["mode"] == "debug_transcript"]
        assert len(synthetic_calls) == 9
        assert all(call["transcript_streaming"] is False for call in synthetic_calls)
        assert all(call["transcript_turn_count"] == len(call["transcript"])
                   for call in synthetic_calls)
        assert all(call["last_transcript_at"] for call in synthetic_calls)

        assert not network_attempts
        assert all(not call.get("audio_path") for call in db.where("calls", job_id=job["id"]))
        print("DEMO CAMPAIGN TEST PASSED: batch1 explorer + 9 streamed debug + batch4 closer")
    finally:
        callrunner._run_twilio_batch = original_twilio
        callrunner.httpx.post, callrunner.httpx.get = original_post, original_get


if __name__ == "__main__":
    main()
