"""Offline test for users + per-user job isolation — no API keys needed.

Each user sees ONLY their own profile and jobs: someone else's job answers
404 everywhere, lists never leak, and agent-tools webhooks require the
machine credential used by ElevenLabs.

  .venv/bin/python -m tests.auth_test
"""

import os
import tempfile

# Isolate ALL storage (DB, uploads) in a throwaway dir — tests must never
# pollute the real data/negotiator.db (learned questions, jobs, users).
os.environ.setdefault("NEGOTIATOR_DATA_DIR", tempfile.mkdtemp(prefix="negotiator-test-"))
os.environ.setdefault("AGENT_TOOL_SECRET", "offline-test-tool-secret")
import uuid

from fastapi.testclient import TestClient

from negotiator.config import AGENT_TOOL_SECRET
from negotiator.server import app

c = TestClient(app)


def register(tag: str) -> tuple[dict, dict]:
    r = c.post("/api/auth/register", json={
        "email": f"{tag}@test.dev", "password": "secret123", "name": tag})
    r.raise_for_status()
    body = r.json()
    return body["user"], {"Authorization": f"Bearer {body['token']}"}


def main():
    tag = uuid.uuid4().hex[:6]
    alice, ha = register(f"alice-{tag}")
    bob, hb = register(f"bob-{tag}")

    # --- registration rules --------------------------------------------------
    assert c.post("/api/auth/register", json={
        "email": f"alice-{tag}@test.dev", "password": "secret123"}).status_code == 409
    assert c.post("/api/auth/register", json={
        "email": f"x-{tag}@test.dev", "password": "ab"}).status_code == 422
    assert "password_hash" not in alice and "salt" not in alice
    print(f"register OK: {alice['email']} / {bob['email']} (no secrets in responses)")

    # --- login ---------------------------------------------------------------
    assert c.post("/api/auth/login", json={
        "email": alice["email"], "password": "WRONG"}).status_code == 401
    r = c.post("/api/auth/login", json={"email": alice["email"], "password": "secret123"})
    assert r.status_code == 200 and r.json()["user"]["id"] == alice["id"]
    print("login OK: wrong password 401, right password issues a token")

    # --- no token / bad token = locked out -----------------------------------
    assert c.get("/api/jobs").status_code == 401
    assert c.get("/api/me", headers={"Authorization": "Bearer nope"}).status_code == 401

    # --- each user creates a job; lists and profiles never leak --------------
    job_a = c.post("/api/jobs", json={"vertical": "plumbing"}, headers=ha).json()
    job_b = c.post("/api/jobs", json={"vertical": "plumbing"}, headers=hb).json()
    assert job_a["user_id"] == alice["id"]

    mine = c.get("/api/jobs", headers=ha).json()
    assert [j["id"] for j in mine] == [job_a["id"]], "alice must see exactly her one job"
    me = c.get("/api/me", headers=ha).json()
    assert me["user"]["id"] == alice["id"] and [j["id"] for j in me["jobs"]] == [job_a["id"]]
    print("scoping OK: /api/jobs and /api/me return only the owner's jobs")

    # --- someone else's job is a 404 on every route --------------------------
    for method, path in [("GET", f"/api/jobs/{job_b['id']}"),
                         ("POST", f"/api/jobs/{job_b['id']}/confirm"),
                         ("PUT", f"/api/jobs/{job_b['id']}/spec"),
                         ("GET", f"/api/jobs/{job_b['id']}/quotes"),
                         ("GET", f"/api/jobs/{job_b['id']}/report")]:
        r = c.request(method, path, headers=ha, json={"spec": {}} if method == "PUT" else None)
        assert r.status_code == 404, f"{method} {path} leaked: {r.status_code}"
    assert c.get(f"/api/jobs/{job_b['id']}", headers=hb).status_code == 200
    print("isolation OK: bob's job answers 404 to alice on every route")

    # --- the reusable demo PDF is authenticated and valid -------------------
    assert c.get("/api/demo/intake-pdf").status_code == 401
    demo_pdf = c.get("/api/demo/intake-pdf", headers=ha)
    assert demo_pdf.status_code == 200
    assert demo_pdf.headers["content-type"].startswith("application/pdf")
    assert demo_pdf.content.startswith(b"%PDF-1.4")
    print("demo PDF OK: authenticated reusable intake asset")

    # --- logout kills the token ---------------------------------------------
    c.post("/api/auth/logout", headers=ha)
    assert c.get("/api/me", headers=ha).status_code == 401
    print("logout OK: token revoked")

    # --- agent tools reject browsers and accept the provisioned machine key --
    r = c.post("/agent-tools/get_intake_form", json={"job_id": job_b["id"]})
    assert r.status_code == 401
    r = c.post("/agent-tools/get_intake_form",
               headers={"X-QuoteWise-Tool-Key": AGENT_TOOL_SECRET},
               json={"job_id": job_b["id"]})
    assert r.status_code == 200, "provisioned ElevenLabs tool key must be accepted"
    print("agent-tools OK: machine key required; browser/user tokens are insufficient")

    print("\nAUTH TEST PASSED")


if __name__ == "__main__":
    main()
