"""Offline test for the call queue — no API keys needed (no real calls).

Covers: seeding the simulated market per job (3 negotiation styles,
idempotent), live status derivation (to_call -> calling -> outcome with
totals), and the guards on starting calls (confirmation gate, eligibility).

  .venv/bin/python -m tests.callqueue_test
"""

import os
import tempfile

# Isolate ALL storage (DB, uploads) in a throwaway dir — tests must never
# pollute the real data/negotiator.db (learned questions, jobs, users).
os.environ.setdefault("NEGOTIATOR_DATA_DIR", tempfile.mkdtemp(prefix="negotiator-test-"))
os.environ.setdefault("AGENT_TOOL_SECRET", "offline-test-tool-secret")
import uuid

from fastapi.testclient import TestClient

from negotiator import db
from negotiator.config import AGENT_TOOL_SECRET
from negotiator.server import app

c = TestClient(app)
TOOL_H = {"X-QuoteWise-Tool-Key": AGENT_TOOL_SECRET}

SPEC = {"area_code": "28202", "job_type": "water_heater",
        "problem_description": "leaking heater", "property_type": "house",
        "urgency": "this_week", "access": {"floor": 1}}


def main():
    r = c.post("/api/auth/register", json={
        "email": f"queue-{uuid.uuid4().hex[:6]}@test.dev", "password": "secret123"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    job = c.post("/api/jobs", json={"vertical": "plumbing"}, headers=h).json()
    c.put(f"/api/jobs/{job['id']}/spec", json={"spec": SPEC}, headers=h).raise_for_status()

    # --- simulated market: 3 styles, idempotent -----------------------------
    r = c.post(f"/api/jobs/{job['id']}/companies/simulated", headers=h).json()
    assert len(r["created"]) == 3, r
    names = {co["name"] for co in r["companies"]}
    assert {"Iron Pipe Plumbing Co.", "QuickFix Drain & Plumbing",
            "Crown Plumbing & Home Services"} == names
    r2 = c.post(f"/api/jobs/{job['id']}/companies/simulated", headers=h).json()
    assert r2["created"] == [], "second seed must be a no-op"
    print("simulated market OK: 3 plumbing styles, idempotent")

    # --- guards --------------------------------------------------------------
    r = c.post(f"/api/jobs/{job['id']}/calls/start", json={"phase": "quote"}, headers=h)
    assert r.status_code == 409, "must refuse calls before user confirmation"
    c.post(f"/api/jobs/{job['id']}/confirm", headers=h).raise_for_status()
    r = c.post(f"/api/jobs/{job['id']}/calls/start",
               json={"phase": "negotiate"}, headers=h)
    assert r.status_code == 404, "negotiate with no quotes must find no eligible companies"
    r = c.post(f"/api/jobs/{job['id']}/calls/start", json={"phase": "bogus"}, headers=h)
    assert r.status_code == 422
    print("guards OK: confirmation gate, negotiate needs quotes, phase validated")

    # --- status derivation from real records ---------------------------------
    q = c.get(f"/api/jobs/{job['id']}/call-queue", headers=h).json()
    assert q["running"] is False and all(row["status"] == "to_call" for row in q["queue"])
    cos = {row["company"]["persona"]: row["company"]["id"] for row in q["queue"]}

    # a call in flight -> calling
    db.put("calls", "call_t1", {"id": "call_t1", "job_id": job["id"],
                                "company_id": cos["lowballer"], "kind": "quote",
                                "started_at": "2026-07-18T20:00:00Z"},
           job_id=job["id"], company_id=cos["lowballer"])
    q = c.get(f"/api/jobs/{job['id']}/call-queue", headers=h).json()
    by_persona = {r["company"]["persona"]: r for r in q["queue"]}
    assert by_persona["lowballer"]["status"] == "calling"

    # quote logged + call ended -> outcome status with totals
    c.post("/agent-tools/log_quote", headers=TOOL_H, json={
        "job_id": job["id"], "company_id": cos["lowballer"], "phase": "initial",
        "total": 900, "binding": False,
        "line_items": [{"label": "all-in", "code": "base", "amount": 900, "kind": "base"}]})
    c.post("/agent-tools/log_call_outcome", headers=TOOL_H, json={
        "job_id": job["id"], "company_id": cos["lowballer"], "outcome": "quote"})
    call = db.get("calls", "call_t1")
    call["ended_at"] = "2026-07-18T20:05:00Z"
    db.put("calls", "call_t1", call, job_id=job["id"], company_id=cos["lowballer"])

    q = c.get(f"/api/jobs/{job['id']}/call-queue", headers=h).json()
    row = {r["company"]["persona"]: r for r in q["queue"]}["lowballer"]
    assert row["status"] == "quote" and row["initial_total"] == 900
    assert len(row["red_flags"]) >= 2, "lowball non-binding quote must carry red flags"
    print("status derivation OK: to_call -> calling -> quote with totals + red flags")

    print("\nCALL QUEUE TEST PASSED")


if __name__ == "__main__":
    main()
