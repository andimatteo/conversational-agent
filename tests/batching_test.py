"""Offline end-to-end test for transcript-only sqrt(n) scheduling."""
import math
import os
import tempfile
import time
import uuid

os.environ.setdefault("NEGOTIATOR_DATA_DIR", tempfile.mkdtemp(prefix="batching-test-"))
os.environ["DEBUG_CALLS"] = "true"
os.environ.setdefault("AGENT_TOOL_SECRET", "offline-test-tool-secret")

from fastapi.testclient import TestClient

from negotiator import db
from negotiator.server import app


SPEC = {
    "area_code": "28202", "job_type": "water_heater",
    "problem_description": "40 gallon tank is leaking at the base",
    "property_type": "house", "urgency": "this_week",
    "access": {"floor": 1, "crawlspace": False, "slab_foundation": False,
               "tight_access": False},
}


def _wait(client, url, headers, timeout=8):
    deadline = time.time() + timeout
    state = None
    while time.time() < deadline:
        state = client.get(url, headers=headers).json()
        if not state["running"]:
            return state
        time.sleep(0.02)
    raise AssertionError(f"run did not finish: {state}")


def main():
    client = TestClient(app)
    auth = client.post("/api/auth/register", json={
        "email": f"batch-{uuid.uuid4().hex[:8]}@test.dev", "password": "secret123"}).json()
    headers = {"Authorization": f"Bearer {auth['token']}"}
    job = client.post("/api/jobs", json={"vertical": "plumbing"}, headers=headers).json()
    client.put(f"/api/jobs/{job['id']}/spec", json={"spec": SPEC}, headers=headers).raise_for_status()
    client.post(f"/api/jobs/{job['id']}/confirm", headers=headers).raise_for_status()

    stored_job = db.get("jobs", job["id"])
    stored_job["call_list"] = {
        "complete": True, "saved": True, "items": [
            {"name": f"Verified Google Vendor {index + 1}",
             "phone": f"+17045550{100 + index}",
             "sources": ["google_places"],
             "source_ids": {"google_places": f"places/{index + 1}"},
             "rating": 4.9 - index / 20, "review_count": 100 + index}
            for index in range(10)
        ]}
    db.put("jobs", job["id"], stored_job)

    initial_request_key = f"initial-{uuid.uuid4()}"
    response = client.post(f"/api/jobs/{job['id']}/calls/start",
                           json={"phase": "quote", "idempotency_key": initial_request_key},
                           headers=headers)
    response.raise_for_status()
    started = response.json()
    assert started["debug_mode"] is True
    assert started["batch_size"] == math.ceil(math.sqrt(10)) == 4
    assert started["batch_count"] == 3 and started["total"] == 10

    state = _wait(client, f"/api/jobs/{job['id']}/call-queue", headers)
    calls = db.where("calls", job_id=job["id"])
    quotes = db.where("quotes", job_id=job["id"])
    companies = db.where("companies", job_id=job["id"])
    batches = sorted(db.where("call_batches", job_id=job["id"]), key=lambda b: b["index"])
    assert [len(b["company_ids"]) for b in batches] == [4, 4, 2]
    assert [b["knowledge_version"] for b in batches] == [0, 1, 2]
    assert all(b["status"] == "completed" for b in batches)
    assert len(calls) == len(quotes) == 10
    assert len(companies) == 10 and all(c["source"] == "google_places" for c in companies)
    assert {c["external_ids"]["google_places"] for c in companies} == {
        f"places/{index + 1}" for index in range(10)}
    assert all(c["ended_at"] and c["debug_generated"] and not c["audio_path"]
               and not c["conversation_id"] for c in calls)
    assert all(c["transcript_kind"] == "debug_generated" and c["learning_analysis"]["logged"]
               for c in calls)

    # All peers in one batch see exactly the prior completed batches, never a
    # sibling result that happened to finish a millisecond earlier.
    known_counts = {}
    for call in calls:
        known_counts.setdefault(call["batch_index"], set()).add(
            len(call["knowledge_snapshot"]["competing_quotes"]))
    assert known_counts == {1: {0}, 2: {4}, 3: {8}}, known_counts
    assert state["summary"]["called"] == 10 and state["summary"]["total"] == 10
    assert state["summary"]["current_best_offer"] and state["summary"]["offer_range"]["count"] == 10
    assert state["follow_up_plan"], "batch completion should create explainable recall suggestions"

    replay = client.post(f"/api/jobs/{job['id']}/calls/start", json={
        "phase": "quote", "idempotency_key": initial_request_key}, headers=headers)
    replay.raise_for_status()
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["run_id"] == started["run_id"] and len(db.where(
        "calls", job_id=job["id"])) == 10

    # Recall the highest vendor. Its concession may only use the frozen own
    # history and a lower competing quote id from the database.
    highest = max(quotes, key=lambda q: q["total"])
    response = client.post(f"/api/jobs/{job['id']}/calls/start", json={
        "phase": "negotiate", "company_ids": [highest["company_id"]]}, headers=headers)
    response.raise_for_status()
    _wait(client, f"/api/jobs/{job['id']}/call-queue", headers)
    negotiated = [q for q in db.where("quotes", job_id=job["id"],
                                      company_id=highest["company_id"])
                  if q["phase"] == "negotiated"]
    assert len(negotiated) == 1 and negotiated[0]["total"] <= highest["total"]
    assert negotiated[0]["leverage_quote_ids"], negotiated[0]
    allowed = {q["id"] for q in quotes if q["company_id"] != highest["company_id"]}
    assert set(negotiated[0]["leverage_quote_ids"]) <= allowed
    state = client.get(f"/api/jobs/{job['id']}/call-queue", headers=headers).json()
    assert state["summary"]["called"] == 10 and state["summary"]["total"] == 10

    # A second callback is allowed; a third is rejected server-side even when
    # the caller explicitly selects the vendor again.
    second = client.post(f"/api/jobs/{job['id']}/calls/start", json={
        "phase": "negotiate", "company_ids": [highest["company_id"]]}, headers=headers)
    second.raise_for_status()
    _wait(client, f"/api/jobs/{job['id']}/call-queue", headers)
    third = client.post(f"/api/jobs/{job['id']}/calls/start", json={
        "phase": "negotiate", "company_ids": [highest["company_id"]]}, headers=headers)
    assert third.status_code == 404 and "hard limit" in third.text
    state = client.get(f"/api/jobs/{job['id']}/call-queue", headers=headers).json()
    recalled_row = next(row for row in state["queue"]
                        if row["company"]["id"] == highest["company_id"])
    assert recalled_row["recalls_used"] == recalled_row["recalls_max"] == 2

    print("BATCHING TEST PASSED: 10 vendors -> 4/4/2, frozen knowledge, max-two recall cap")


if __name__ == "__main__":
    main()
