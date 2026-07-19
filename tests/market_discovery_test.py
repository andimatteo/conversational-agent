"""Offline call-list tests: python -m tests.market_discovery_test"""
import os
import tempfile
import uuid

os.environ.setdefault("NEGOTIATOR_DATA_DIR", tempfile.mkdtemp(prefix="call-list-test-"))

from fastapi.testclient import TestClient

from market_discovery.models import Business, StateArea
from market_discovery.router import get_discovery_service
from market_discovery.service import DiscoveryService
from negotiator.server import app


class FakeGeocoder:
    def resolve_state(self, state):
        return StateArea("North Carolina", "NC", 33.8, -84.3, 36.6, -75.4)


class FakeProvider:
    def __init__(self, name, rows=None, error=False):
        self.name, self.rows, self.error = name, rows or [], error

    def enabled(self):
        return True

    def search(self, query, area, target):
        if self.error:
            raise RuntimeError("temporary upstream failure")
        return self.rows[:target]


def _service_result():
    google = FakeProvider("google_places", [
        Business("Fast Flow Plumbing", "(704) 555-0100", "google_places", "g-1",
                 city="Charlotte", state="NC", rating=4.8),
        Business("No Phone LLC", source="google_places", state="NC"),
    ])
    yelp = FakeProvider("yelp", [
        Business("FastFlow Plumbing", "+1 704 555 0100", "yelp", "y-1",
                 city="Charlotte", state="NC", review_count=120),
        Business("Statewide Rooter", "919.555.0199", "yelp", "y-2",
                 city="Raleigh", state="NC", rating=4.4),
    ])
    return DiscoveryService(
        [google, yelp, FakeProvider("openstreetmap", error=True)], FakeGeocoder()
    ).discover("plumbing company", "North Carolina", 100)


class FakeService:
    def discover(self, query, state, target_per_provider):
        result = _service_result()
        result["query"] = query
        result["provider_status"]["openstreetmap"] = {"status": "ok", "results": 0}
        result["complete"] = True
        return result


class PartialFakeService:
    def discover(self, query, state, target_per_provider):
        return _service_result()


def _auth(client):
    response = client.post("/api/auth/register", json={
        "email": f"call-list-{uuid.uuid4().hex[:8]}@test.dev",
        "password": "secret123",
    })
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['token']}"}


def main():
    result = _service_result()
    assert result["raw_results"] == 4
    assert result["total"] == 2, result
    first = result["items"][0]
    assert first["phone"] == "+17045550100"
    assert first["sources"] == ["google_places", "yelp"]
    assert first["source_ids"] == {"google_places": "g-1", "yelp": "y-1"}
    assert first["review_count"] == 120
    assert result["required_sources"] == ["google_places", "yelp", "openstreetmap"]
    assert result["complete"] is False
    assert result["provider_status"]["openstreetmap"]["status"] == "error"
    try:
        DiscoveryService([
            FakeProvider("google_places"), FakeProvider("yelp")
        ], FakeGeocoder()).discover("plumbing company", "North Carolina")
        raise AssertionError("discovery accepted a configuration without OSM")
    except ValueError as exc:
        assert "openstreetmap" in str(exc)

    app.dependency_overrides[get_discovery_service] = lambda: FakeService()
    client = TestClient(app)
    headers = _auth(client)
    job = client.post("/api/jobs", json={"vertical": "plumbing"}, headers=headers).json()
    response = client.post(
        f"/api/jobs/{job['id']}/call-list/discover",
        json={"state": "North Carolina", "target_per_provider": 100},
        headers=headers,
    )
    response.raise_for_status()
    assert response.json()["query"] == "plumbing company"
    assert response.json()["complete"] is True and response.json()["saved"] is True
    forbidden_subset = client.post(
        f"/api/jobs/{job['id']}/call-list/discover",
        json={"state": "North Carolina", "providers": ["openstreetmap"]},
        headers=headers,
    )
    assert forbidden_subset.status_code == 422, forbidden_subset.text
    saved = client.get(f"/api/jobs/{job['id']}/call-list", headers=headers)
    saved.raise_for_status()
    assert saved.json()["total"] == 2
    assert len(saved.json()["items"]) == 2
    app.dependency_overrides[get_discovery_service] = lambda: PartialFakeService()
    partial = client.post(
        f"/api/jobs/{job['id']}/call-list/discover",
        json={"state": "North Carolina"}, headers=headers,
    )
    assert partial.json()["complete"] is False and partial.json()["saved"] is False
    still_saved = client.get(f"/api/jobs/{job['id']}/call-list", headers=headers).json()
    assert still_saved["complete"] is True, "partial search overwrote the complete call list"
    assert client.get(f"/api/jobs/{job['id']}/call-list", headers=_auth(client)).status_code == 404
    app.dependency_overrides.clear()
    print("MARKET DISCOVERY TEST PASSED")


if __name__ == "__main__":
    main()
