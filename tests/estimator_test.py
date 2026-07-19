"""Offline test for the domain-sheet Estimator module — no API keys needed.

Covers the plumbing MVP end to end:
  sheet loading/validation, job creation per (vertical, area), the intake form
  (base + learned questions), the learned-questions loop with dedup, the generic
  rate-card benchmark with modifiers, spec guard + red flags on the plumbing pack.

  .venv/bin/python -m tests.estimator_test
"""

import os
import tempfile

# Isolate ALL storage (DB, uploads) in a throwaway dir — tests must never
# pollute the real data/negotiator.db (learned questions, jobs, users).
os.environ.setdefault("NEGOTIATOR_DATA_DIR", tempfile.mkdtemp(prefix="negotiator-test-"))
import uuid

from fastapi.testclient import TestClient

from negotiator.benchmarks import market_range
from negotiator.packs import list_packs, load_pack, validate_pack
from negotiator.server import app

c = TestClient(app)


def _auth() -> dict:
    r = c.post("/api/auth/register", json={
        "email": f"estimator-{uuid.uuid4().hex[:6]}@test.dev", "password": "secret123"})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['token']}"}


PLUMBING_SPEC = {
    "vertical": "plumbing",
    "area_code": "28202",
    "job_type": "water_heater",
    "problem_description": "40-gal gas water heater leaking from the tank base",
    "property_type": "house",
    "property_age_years": 60,
    "urgency": "within_24h",
    "water_shutoff_known": True,
    "access": {"floor": 0, "crawlspace": True, "slab_foundation": False, "tight_access": True},
    "fixtures_affected": [{"fixture": "water heater", "issue": "leaking"}],
    "pipe_material": "copper",
    "prior_repair_attempted": False,
    "photos_available": True,
}


def main():
    # --- sheets on disk load and validate ----------------------------------
    packs = {(p["vertical"], p["area_code"]): p for p in list_packs()}
    assert ("plumbing", "28202") in packs and ("moving", "") in packs
    assert all(p["valid"] for p in packs.values()), f"invalid sheets: {packs}"
    plumbing = load_pack("plumbing", "28202")
    broken = {k: v for k, v in plumbing.items() if k != "benchmark"}
    assert any("benchmark" in e for e in validate_pack(broken)), "validator missed a broken sheet"
    print(f"sheets OK: {sorted(packs)}")

    # unknown area falls back to the domain's sheet (new areas work day one)
    assert load_pack("plumbing", "99999")["meta"]["vertical"] == "plumbing"

    # --- job creation picks vertical + area from the sheet ------------------
    h = _auth()
    job = c.post("/api/jobs", json={"vertical": "plumbing"}, headers=h).json()
    assert job["vertical"] == "plumbing" and job["area_code"] == "28202"
    assert c.post("/api/jobs", json={"vertical": "does-not-exist"}, headers=h).status_code == 404
    print(f"job {job['id']} on plumbing/28202")

    # --- intake form = base questions (+ learned, none required yet) --------
    form = c.get("/api/intake-form", params={"vertical": "plumbing"}).json()
    assert form["base_questions"] == plumbing["estimator_questions"]
    assert form["area_code"] == "28202" and "spec_schema" in form
    r = c.post("/agent-tools/get_intake_form", json={"job_id": job["id"]})
    assert r.json()["base_questions"] == plumbing["estimator_questions"]
    print(f"intake form: {len(form['base_questions'])} base + {len(form['learned_questions'])} learned")

    # --- estimator saves partial spec -> missing fields, no confirmation ----
    partial = {k: v for k, v in PLUMBING_SPEC.items() if k not in ("urgency", "access")}
    r = c.post("/agent-tools/save_job_spec", json={"job_id": job["id"], "spec": partial}).json()
    assert set(r["missing_required_fields"]) == {"urgency", "access"}
    assert c.post("/agent-tools/get_job_spec", json={"job_id": job["id"]}).status_code == 409

    # the interview only asks what's missing: the form tool exposes what's on file
    r = c.post("/agent-tools/get_intake_form", json={"job_id": job["id"]}).json()
    assert set(r["missing_required_fields"]) == {"urgency", "access"}
    assert r["already_on_file"]["job_type"] == "water_heater"
    print("ask-only-missing OK: form tool reports already_on_file + missing fields")

    r = c.post("/agent-tools/save_job_spec", json={"job_id": job["id"], "spec": PLUMBING_SPEC}).json()
    assert r["missing_required_fields"] == []
    # a later partial save can never wipe known fields with empties (False is a real answer)
    c.post("/agent-tools/save_job_spec",
           json={"job_id": job["id"], "spec": {"pipe_material": "", "water_shutoff_known": False}})
    spec_now = c.get(f"/api/jobs/{job['id']}", headers=h).json()["spec"]
    assert spec_now["pipe_material"] == "copper" and spec_now["water_shutoff_known"] is False
    print("no-clobber OK: empty values dropped on save, False/0 kept")

    c.post(f"/api/jobs/{job['id']}/confirm", headers=h).raise_for_status()
    assert c.post("/agent-tools/get_job_spec", json={"job_id": job["id"]}).status_code == 200
    print("spec guard OK: 409 until saved complete + user-confirmed")

    # --- generic rate-card benchmark + modifiers ----------------------------
    bench = c.post("/agent-tools/get_benchmark", json={"job_id": job["id"]}).json()
    # water_heater: 90 + 4h*125 + 950 = 1540, then within_24h/old-building/tight-access modifiers
    assert bench["base_estimate"] == round(1540 * 1.15 * 1.15 * 1.10, 2), bench
    emergency = market_range({**PLUMBING_SPEC, "urgency": "emergency"}, plumbing)
    assert emergency["median"] > bench["median"], "emergency modifier must raise the price"
    print(f"benchmark OK: fair ${bench['fair_low']}-${bench['fair_high']}, median ${bench['median']} "
          f"(emergency median ${emergency['median']})")

    # --- learned-questions loop: log -> dedupe -> next form includes them ---
    tag = uuid.uuid4().hex[:6]
    qa = f"Is the water heater in a code-compliant drain pan? [{tag}]"
    qb = f"Does the HOA require a licensed contractor certificate? [{tag}]"
    r = c.post("/agent-tools/log_learned_questions", json={
        "job_id": job["id"],
        "questions": [{"question": qa, "why_it_matters": "Pan + expansion tank add $150-$300"},
                      {"question": f"  {qa.upper()} "},  # same question, messier phrasing
                      {"question": qb, "why_it_matters": "Permit/cert handling adds fees"},
                      {"question": plumbing["estimator_questions"][0]}],  # already a base question
    }).json()
    assert [q["question"] for q in r["added"]] == [qa, qb], r
    assert len(r["already_known"]) == 2
    # surfaced to the user on the job record
    assert [q["question"] for q in c.get(f"/api/jobs/{job['id']}", headers=h).json()["discovered_questions"]] == [qa, qb]
    # a NEW job in the same area now gets them in its form; times_seen dedupe works
    r = c.post("/agent-tools/log_learned_questions",
               json={"job_id": job["id"], "questions": [{"question": qa}]}).json()
    assert r["added"] == []
    job2 = c.post("/api/jobs", json={"vertical": "plumbing"}, headers=h).json()
    form2 = c.post("/agent-tools/get_intake_form", json={"job_id": job2["id"]}).json()
    learned = {q["question"]: q for q in form2["learned_questions"]}
    # times_seen: initial add (1) + messy duplicate in call 1 (+1) + call 2 (+1)
    assert qa in learned and qb in learned and learned[qa]["times_seen"] == 3
    # ...but never leaks into another domain/area
    moving_form = c.get("/api/intake-form", params={"vertical": "moving"}).json()
    assert qa not in [q["question"] for q in moving_form["learned_questions"]]
    print(f"learned loop OK: 2 added, deduped, times_seen=3, visible to next job in area, "
          f"isolated from moving")

    # --- red flags evaluate against the plumbing pack -----------------------
    co = {"id": "co_test_" + tag, "name": "Drainz4Less", "persona": "", "source": "manual"}
    from negotiator import db
    db.put("companies", co["id"], co, job_id=job["id"])
    r = c.post("/agent-tools/log_quote", json={
        "job_id": job["id"], "company_id": co["id"], "phase": "initial",
        "total": round(bench["median"] * 0.5), "binding": False,
        "line_items": [{"label": "all-in", "code": "base", "amount": round(bench["median"] * 0.5),
                        "kind": "base"}],
        "verbatim_evidence": "Thousand bucks flat, we'll sort it out on site."}).json()
    flags = {f["id"] for f in r["red_flags"]}
    assert {"too_low", "non_binding", "no_itemization"} <= flags, flags
    print(f"red flags OK on plumbing quote: {sorted(flags)}")

    print("\nESTIMATOR TEST PASSED")


if __name__ == "__main__":
    main()
