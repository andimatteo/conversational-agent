"""Offline reconciliation tests for ElevenLabs/Twilio terminal states."""
import os
import tempfile

os.environ.setdefault("NEGOTIATOR_DATA_DIR", tempfile.mkdtemp(prefix="provider-status-test-"))

from negotiator import callrunner, db
from negotiator.knowledge import context_for, create_snapshot
from negotiator.models import Company, Job
from negotiator.seed import SAMPLE_SPEC
import simulation.run_calls as simulation_calls


class Response:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code
        self.text = str(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 300:
            raise RuntimeError(f"HTTP {self.status_code}")


def _put_call(call_id, company_id, **extra):
    row = {"id": call_id, "job_id": "job_provider", "company_id": company_id,
           "kind": "quote", "status": "queued", "batch_id": "batch_provider",
           "knowledge_version": 0, "created_at": callrunner.now(), **extra}
    db.put("calls", call_id, row, job_id="job_provider", company_id=company_id)
    return row


def test_recipient_matching():
    rows = [
        ({"id": "call_a"}, {"phone": "+15550000000"}),
        ({"id": "call_b"}, {"phone": "+15550000000"}),
    ]
    dynamic = {"id": "provider-replaced-id", "phone_number": "+15550000000",
               "conversation_initiation_client_data": {
                   "dynamic_variables": {"call_id": "call_b"}}}
    assert callrunner._recipient_call(rows, dynamic)["id"] == "call_b"
    assert callrunner._recipient_call(rows, {"phone_number": "+15550000000"}) is None
    assert callrunner._recipient_call(rows, {"id": "call_a"})["id"] == "call_a"


def test_fail_repairs_zombie_and_preserves_outcome():
    _put_call("call_zombie", "co_zombie", status="calling",
              ended_at="2026-07-18T00:00:00Z", outcome="callback",
              callback_time="tomorrow")
    callrunner._fail_call("call_zombie", "provider failed", reason="provider_cancelled",
                          external_status="cancelled")
    row = db.get("calls", "call_zombie")
    assert row["status"] == "failed" and row["terminal_reason"] == "provider_cancelled"
    assert row["outcome"] == "callback" and row["callback_time"] == "tomorrow"
    assert row["external_status"] == "cancelled"


def test_mixed_provider_batch_never_finalizes_empty_conversation():
    companies = [
        {"id": "co_a", "name": "A", "phone": "+15550000001"},
        {"id": "co_b", "name": "B", "phone": "+15550000002"},
        {"id": "co_c", "name": "C", "phone": "+15550000003"},
    ]
    calls = [_put_call(f"call_{letter}", f"co_{letter}") for letter in "abc"]
    rows = list(zip(calls, companies))
    batch = {"id": "batch_provider", "run_id": "run_provider", "job_id": "job_provider",
             "index": 1}
    db.put("call_batches", batch["id"], {**batch, "status": "running"},
           job_id=batch["job_id"], run_id=batch["run_id"])

    old_post, old_get = callrunner.httpx.post, callrunner.httpx.get
    old_finalize = simulation_calls._finalize_call
    finalized = []

    def fake_post(url, **kwargs):
        assert url.endswith("/batch-calling/submit")
        return Response({"id": "provider_batch"})

    def fake_get(url, **kwargs):
        return Response({
            "status": "failed",
            "recipients": [
                {"id": "call_a", "status": "completed", "conversation_id": "conv_a"},
                {"id": "provider-id-b", "status": "failed", "conversation_id": None,
                 "conversation_initiation_client_data": {
                     "dynamic_variables": {"call_id": "call_b"}}},
            ],
        })

    def fake_finalize(call_id, job_id, company_id, conversation_id):
        assert conversation_id, "provider reconciliation must never finalize an empty id"
        finalized.append((call_id, conversation_id))
        row = db.get("calls", call_id)
        row.update({"status": "completed", "outcome": "quote", "ended_at": callrunner.now(),
                    "conversation_id": conversation_id})
        db.put("calls", call_id, row, job_id=job_id, company_id=company_id)

    try:
        callrunner.httpx.post, callrunner.httpx.get = fake_post, fake_get
        simulation_calls._finalize_call = fake_finalize
        callrunner._run_twilio_batch(rows, batch)
    finally:
        callrunner.httpx.post, callrunner.httpx.get = old_post, old_get
        simulation_calls._finalize_call = old_finalize

    assert finalized == [("call_a", "conv_a")]
    assert db.get("calls", "call_b")["terminal_reason"] == "provider_recipient_failed"
    assert db.get("calls", "call_c")["terminal_reason"] == "missing_recipient"
    stored_batch = db.get("call_batches", batch["id"])
    assert stored_batch["provider_status"] == "failed"
    assert stored_batch["succeeded"] == 1 and stored_batch["failed"] == 2


def test_artifact_failure_still_reaches_terminal_state():
    job = Job(id="job_artifact", vertical="moving", spec=SAMPLE_SPEC,
              spec_source="sample", confirmed=True)
    db.put("jobs", job.id, job.model_dump())
    company = Company(id="co_artifact", name="Artifact Vendor")
    db.put("companies", company.id, company.model_dump(), job_id=job.id)
    call = {"id": "call_artifact", "job_id": job.id, "company_id": company.id,
            "kind": "quote", "status": "calling", "outcome": "callback",
            "callback_time": "tomorrow", "created_at": callrunner.now(),
            "knowledge_snapshot": context_for(create_snapshot(job.id, 0), company.id)}
    db.put("calls", call["id"], call, job_id=job.id, company_id=company.id)
    old_fetch = simulation_calls._fetch_conversation
    try:
        simulation_calls._fetch_conversation = lambda *_: (_ for _ in ()).throw(
            RuntimeError("artifact service unavailable"))
        simulation_calls._finalize_call(call["id"], job.id, company.id, "conv_artifact")
    finally:
        simulation_calls._fetch_conversation = old_fetch
    terminal = db.get("calls", call["id"])
    assert terminal["status"] == "completed" and terminal.get("ended_at")
    assert terminal["outcome"] == "callback"
    assert "artifact service unavailable" in terminal["artifact_errors"][0]


def main():
    test_recipient_matching()
    test_fail_repairs_zombie_and_preserves_outcome()
    test_mixed_provider_batch_never_finalizes_empty_conversation()
    test_artifact_failure_still_reaches_terminal_state()
    print("PROVIDER STATUS TEST PASSED: correlation, mixed outcomes, zombie repair")


if __name__ == "__main__":
    main()
