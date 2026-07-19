"""Offline review → live-Places boundary → automatic campaign test.

The Places dependency and call scheduler are fakes, so this test proves the
orchestration contract without network access or telephony.
"""
from __future__ import annotations

import os
import tempfile
import uuid

os.environ["NEGOTIATOR_DATA_DIR"] = tempfile.mkdtemp(prefix="demo-launch-test-")
os.environ["DEBUG_CALLS"] = "true"
os.environ["LIVE_VENDOR_CALLS_ENABLED"] = "false"
os.environ["DEMO_PHONE_NUMBER"] = "+16505550199"
os.environ["ELEVENLABS_PHONE_NUMBER_ID"] = "phone_test_never_submitted"
os.environ["ELEVENLABS_API_KEY"] = "test_key_never_submitted"
os.environ["VERTICAL"] = "plumbing"
os.environ.setdefault("AGENT_TOOL_SECRET", "offline-test-tool-secret")

from fastapi.testclient import TestClient

from market_discovery.router import get_discovery_service
from negotiator import callrunner, db, demo_reset
from negotiator.server import app


ITEMS = [
    {
        "name": name,
        "phone": f"+1704555010{index}",
        "address": f"{index} Trade Street, Charlotte, NC 28202",
        "latitude": 35.22 + index / 100,
        "longitude": -80.84 - index / 100,
        "rating": 4.9 - index / 10,
        "review_count": 100 + index,
        "url": f"https://maps.google.com/?cid={index}",
        "sources": ["google_places"],
        "source_ids": {"google_places": f"places/{name.casefold()}"},
    }
    for index, name in enumerate(("Alpha Plumbing", "Beta Plumbing", "Zulu Plumbing"), 1)
]


class FakeLivePlaces:
    def __init__(self):
        self.calls = []

    def discover_google_places(self, query: str, state: str, target: int):
        self.calls.append((query, state, target))
        return {
            "generated_at": "2026-07-19T12:00:00+00:00",
            "query": query,
            "state": {"name": state, "code": "NC"},
            "target_per_provider": target,
            "required_sources": ["google_places"],
            "complete": True,
            "saved": True,
            "provider_status": {
                "google_places": {"status": "ok", "results": 3, "live_api": True}
            },
            "raw_results": 3,
            "total": 3,
            "items": ITEMS,
            "discovery_mode": "live_google_places_at_launch",
        }


def _quote(job_id: str, company: dict, total: float) -> None:
    quote_id = f"quote_{company['id']}"
    db.put("quotes", quote_id, {
        "id": quote_id,
        "job_id": job_id,
        "company_id": company["id"],
        "call_id": f"call_{company['id']}",
        "phase": "initial",
        "line_items": [{"label": "All-in", "code": "base", "amount": total,
                        "kind": "base"}],
        "total": total,
        "binding": True,
        "deposit": 0,
        "conditions": [],
        "red_flags": [],
        "verbatim_evidence": f"The all-in total is ${total:,.0f}.",
        "evidence_verified": True,
        "grounding_verified": True,
        "itemization_verified": True,
        "evidence_kind": "offline_fixture",
        "created_at": "2026-07-19T12:01:00+00:00",
    }, job_id=job_id, company_id=company["id"], phase="initial")


def main() -> None:
    prepared = demo_reset.reset(live_vendor="Beta")
    job = db.get("jobs", prepared["job_id"])
    job["spec"] = demo_reset.DEMO_SPEC
    job["spec_source"] = "demo+document+interview"
    job["documents"] = [{"id": "doc_demo", "filename": "water-heater.pdf"}]
    db.put("jobs", job["id"], job)

    fake_places = FakeLivePlaces()
    app.dependency_overrides[get_discovery_service] = lambda: fake_places
    original_start = callrunner.start_calls
    starts = []

    def fake_start(job_id: str, phase: str, **kwargs):
        starts.append((job_id, phase, kwargs))
        return {
            "started": True, "run_id": "run_launch", "phase": "quote",
            "total": 3, "total_calls": 4, "batch_size": 2,
            "batch_count": 3, "quote_batch_count": 2,
            "auto_negotiation_batch": 3, "demo_roleplay": True,
            "demo_calls_authorized": True,
        }

    callrunner.start_calls = fake_start
    try:
        client = TestClient(app)
        login = client.post("/api/auth/login", json={
            "email": "demo@negotiator.app", "password": "demo1234",
        })
        login.raise_for_status()
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        launch_key = f"launch-{uuid.uuid4().hex}"

        denied = client.post(f"/api/jobs/{job['id']}/launch", json={
            "authorize_demo_calls": False, "idempotency_key": launch_key,
        }, headers=headers)
        assert denied.status_code == 409
        assert fake_places.calls == [] and starts == []

        injected_phone = client.post(f"/api/jobs/{job['id']}/launch", json={
            "authorize_demo_calls": True, "idempotency_key": launch_key,
            "to_number": "+17045559999",
        }, headers=headers)
        assert injected_phone.status_code == 422
        assert fake_places.calls == [] and starts == []

        response = client.post(f"/api/jobs/{job['id']}/launch", json={
            "authorize_demo_calls": True, "idempotency_key": launch_key,
        }, headers=headers)
        response.raise_for_status()
        body = response.json()
        assert body["discovery"]["live_api"] is True
        assert body["discovery"]["provider"] == "google_places"
        assert body["redirect"] == f"/job/{job['id']}/calls"
        assert len(fake_places.calls) == 1
        assert len(starts) == 1
        assert starts[0][2]["authorize_demo_calls"] is True
        assert starts[0][2]["idempotency_key"] == launch_key

        stored = db.get("jobs", job["id"])
        assert stored["confirmed"] is True
        assert stored["demo_mode"]["live_company_name"] == "Beta Plumbing"
        assert stored["demo_mode"]["discovery"]["live_api"] is True
        companies = db.where("companies", job_id=job["id"])
        assert len(companies) == 3
        assert all(row["source"] == "google_places" for row in companies)
        assert all(row.get("latitude") is not None for row in companies)
        assert all(row["phone"] != "+16505550199" for row in companies)

        totals = {"Alpha Plumbing": 2500, "Beta Plumbing": 2000, "Zulu Plumbing": 3000}
        for company in companies:
            _quote(job["id"], company, totals[company["name"]])
        report = client.get(f"/api/jobs/{job['id']}/report", headers=headers).json()
        points = report["map"]["points"]
        assert len(points) == 3
        preferred = [point for point in points if point["preferred"]]
        assert len(preferred) == 1
        assert preferred[0]["company_name"] == "Beta Plumbing"
        assert preferred[0]["price_label"] == "$2,000"

        print(
            "DEMO LAUNCH TEST PASSED: review -> fresh Places -> promotion -> "
            "automatic campaign + preferred map pin"
        )
    finally:
        callrunner.start_calls = original_start
        app.dependency_overrides.clear()


if __name__ == "__main__":
    main()
