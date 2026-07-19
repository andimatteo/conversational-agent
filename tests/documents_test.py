"""Offline test for document intake — no API keys needed (parser is mocked).

Covers: upload PDFs/photos alongside the call, extracted data COMBINED with
call data in the one spec used for calls (interview wins, docs fill gaps,
deep-fill on nested objects), quotes from documents accumulate as negotiation
leverage, insights land in notes, documents are listed per job, spec changes
force re-confirmation.

  .venv/bin/python -m tests.documents_test
"""

import os
import tempfile

# Isolate ALL storage (DB, uploads) in a throwaway dir — tests must never
# pollute the real data/negotiator.db (learned questions, jobs, users).
os.environ.setdefault("NEGOTIATOR_DATA_DIR", tempfile.mkdtemp(prefix="negotiator-test-"))
import uuid

from fastapi.testclient import TestClient

import negotiator.docparse as docparse
from negotiator.server import app

c = TestClient(app)


def _auth() -> dict:
    r = c.post("/api/auth/register", json={
        "email": f"docs-{uuid.uuid4().hex[:6]}@test.dev", "password": "secret123"})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _fake_parser(payload: dict):
    def fake(filename, content, pack, current_spec):
        assert pack["meta"]["vertical"] == "plumbing", "parser must get the job's pack"
        assert current_spec.get("job_type"), "parser must see the call's data for complementing"
        return payload
    return fake


def main():
    h = _auth()
    job = c.post("/api/jobs", json={"vertical": "plumbing"}, headers=h).json()

    # data from the CALL first (form door, same rules)
    call_spec = {"job_type": "water_heater", "urgency": "within_24h",
                 "area_code": "28202", "problem_description": "heater leaking from tank base",
                 "property_type": "apartment", "access": {"floor": 7}, "notes": "from the call"}
    c.put(f"/api/jobs/{job['id']}/spec", json={"spec": call_spec}, headers=h).raise_for_status()

    # --- doc 1: a competitor's PDF quote that fills gaps AND corrects fields --
    docparse.parse_document = _fake_parser({
        "property_type": "condo",                  # correction -> UPDATE with diff
        "property_age_years": 40,                  # gap -> filled
        "access": {"floor": 2, "slab_foundation": True},  # deep: floor updated, slab filled
        "existing_quote": {"company": "FastFlow Plumbing", "total": 2350,
                           "line_items": [{"label": "heater + install", "amount": 2350}]},
        "insights": ["Water heater is a 40-gal A.O. Smith from 2011"],
    })
    r = c.post(f"/api/jobs/{job['id']}/documents", headers=h,
               files={"file": ("competitor_quote.pdf", b"%PDF-1.4 fake", "application/pdf")})
    r.raise_for_status()
    doc, spec = r.json()["document"], r.json()["job"]["spec"]
    assert spec["property_type"] == "condo", "the document must be able to UPDATE the intake"
    assert spec["property_age_years"] == 40 and spec["job_type"] == "water_heater"
    assert spec["access"] == {"floor": 2, "slab_foundation": True}, spec["access"]
    diffs = {u["field"]: u for u in doc["updates"]}
    assert diffs["property_type"] == {"field": "property_type", "from": "apartment", "to": "condo"}
    assert diffs["access.floor"]["from"] == 7 and diffs["access.floor"]["to"] == 2
    assert "property_age_years" in doc["extracted_fields"] and "access.slab_foundation" in doc["extracted_fields"]
    assert spec["existing_quotes"][0]["company"] == "FastFlow Plumbing"
    assert "from the call" in spec["notes"] and "[doc] Water heater is a 40-gal" in spec["notes"]
    assert doc["has_quote"]
    assert r.json()["job"]["confirmed"] is False, "doc changes must force re-confirmation"
    print("doc 1 OK: gaps filled, fields UPDATED with tracked diffs, quote + insights stored")

    # --- doc 2: a photo adding a second quote --------------------------------
    docparse.parse_document = _fake_parser({
        "existing_quote": {"company": "Budget Rooter", "total": 1100, "line_items": []}})
    c.post(f"/api/jobs/{job['id']}/documents", headers=h,
           files={"file": ("photo.jpg", b"\xff\xd8fake", "image/jpeg")}).raise_for_status()

    docs = c.get(f"/api/jobs/{job['id']}/documents", headers=h).json()
    assert [d["filename"] for d in docs] == ["competitor_quote.pdf", "photo.jpg"]
    print("doc 2 OK: documents listed per job, quotes accumulate")

    # --- the combined spec feeds the calls: leverage includes both quotes ----
    c.post(f"/api/jobs/{job['id']}/confirm", headers=h).raise_for_status()
    r = c.post("/agent-tools/get_competing_quotes",
               json={"job_id": job["id"], "company_id": "co_none"})
    companies = {q["company"]: q for q in r.json()["competing_quotes"]}
    assert companies["FastFlow Plumbing"]["phase"] == "document"
    assert companies["Budget Rooter"]["total"] == 1100
    spec_for_calls = c.post("/agent-tools/get_job_spec", json={"job_id": job["id"]}).json()["spec"]
    assert spec_for_calls["property_age_years"] == 40 and spec_for_calls["job_type"] == "water_heater"
    print("combined spec OK: doc quotes are closer leverage, merged spec feeds the calls")

    print("\nDOCUMENTS TEST PASSED")


if __name__ == "__main__":
    main()
