"""Offline end-to-end smoke test — no API keys needed.

Simulates exactly what the agents do via webhooks: seeds Daniel's move,
logs three personas' quotes (as the Caller would), logs a negotiated
concession (as the Closer would), and checks red flags + report ranking.

  .venv/bin/python -m tests.smoke_test
"""

import os
import tempfile

# Isolate ALL storage (DB, uploads) in a throwaway dir — tests must never
# pollute the real data/negotiator.db (learned questions, jobs, users).
os.environ.setdefault("NEGOTIATOR_DATA_DIR", tempfile.mkdtemp(prefix="negotiator-test-"))
from fastapi.testclient import TestClient

from negotiator import db
from negotiator.benchmarks import market_range
from negotiator.packs import load_pack
from negotiator.seed import SAMPLE_SPEC
from negotiator.models import Company, Job
from negotiator.server import app

c = TestClient(app)


def li(label, code, amount, kind="fee"):
    return {"label": label, "code": code, "amount": amount, "kind": kind}


def main():
    # --- seed user + job + market ------------------------------------------
    import uuid
    r = c.post("/api/auth/register", json={
        "email": f"smoke-{uuid.uuid4().hex[:6]}@test.dev", "password": "secret123"})
    r.raise_for_status()
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    job = Job(id=db.new_id("job"), vertical="moving", spec=SAMPLE_SPEC,
              spec_source="sample", confirmed=False, user_id=r.json()["user"]["id"])
    db.put("jobs", job.id, job.model_dump())

    cos = {}
    for pid, name in [("stonewaller", "Summit & Sons Moving"),
                      ("lowballer", "QuickBudget Movers"),
                      ("upseller", "Premier Coast Van Lines")]:
        co = Company(id=db.new_id("co"), name=name, persona=pid)
        db.put("companies", co.id, co.model_dump(), job_id=job.id)
        cos[pid] = co.id

    # explicit pack: this test is about MOVING regardless of the process
    # default (VERTICAL env) — the server resolves the pack from the job
    bench = market_range(SAMPLE_SPEC, load_pack("moving"))
    print(f"benchmark: {bench}")

    # --- guard: no calls before user confirmation --------------------------
    r = c.post("/agent-tools/get_job_spec", json={"job_id": job.id})
    assert r.status_code == 409, "spec guard failed — calls possible before confirmation!"
    c.post(f"/api/jobs/{job.id}/confirm", headers=h).raise_for_status()
    assert c.post("/agent-tools/get_job_spec", json={"job_id": job.id}).status_code == 200
    print("guard OK: calls locked until spec confirmed")

    # --- counterparty back office serves hidden ground truth ---------------
    r = c.post("/agent-tools/counterparty_pricing",
               json={"job_id": job.id, "company_id": cos["lowballer"]})
    pricing = r.json()
    assert pricing["list_price"] < bench["red_flag_floor"], "lowballer anchor should trip the red flag"
    print(f"lowballer back office: anchor ${pricing['list_price']} (floor ${pricing['floor_price']}, "
          f"{len(pricing['hidden_fees'])} hidden fees)")

    # --- Caller logs three initial quotes ----------------------------------
    med = bench["median"]
    quotes = {
        "stonewaller": dict(total=round(med * 1.05), binding=True, deposit=100,
                            line_items=[li("labor+truck", "base", round(med * 0.85), "base"),
                                        li("fuel", "fuel", round(med * 0.10)),
                                        li("stairs", "stairs", round(med * 0.10))],
                            verbatim_evidence="That's eighteen-sixty all in, and I'll put it in writing."),
        "lowballer": dict(total=round(med * 0.60), binding=False, deposit=round(med * 0.25),
                          line_items=[li("all-in special", "base", round(med * 0.60), "base")],
                          conditions=["price only valid today"],
                          verbatim_evidence="Ten-fifty, all-in, basically. We're the cheapest, trust me."),
        "upseller": dict(total=round(med * 1.45), binding=True, deposit=200,
                         line_items=[li("white glove base", "base", round(med * 1.0), "base"),
                                     li("packing package", "packing", round(med * 0.25), "addon"),
                                     li("premium insurance", "insurance", round(med * 0.20), "addon")],
                         verbatim_evidence="With White Glove you're at twenty-nine hundred, today only."),
    }
    for pid, q in quotes.items():
        r = c.post("/agent-tools/log_quote", json={"job_id": job.id, "company_id": cos[pid],
                                                   "phase": "initial", **q})
        r.raise_for_status()
        flags = [f["id"] for f in r.json()["red_flags"]]
        print(f"{pid}: ${q['total']} -> flags {flags}")
        if pid == "lowballer":
            assert {"too_low", "non_binding", "big_deposit", "no_itemization", "pressure_expiry"} <= set(flags)

    # --- honesty gate: leverage = exactly the DB ---------------------------
    r = c.post("/agent-tools/get_competing_quotes",
               json={"job_id": job.id, "company_id": cos["upseller"]})
    names = [q["company"] for q in r.json()["competing_quotes"]]
    assert "Summit & Sons Moving" in names and "Premier Coast Van Lines" not in names
    print(f"leverage for closer vs upseller: {names}")

    # --- Closer: upseller price-matches Summit -5% -------------------------
    summit = quotes["stonewaller"]["total"]
    matched = round(summit * 0.95)
    c.post("/agent-tools/log_quote", json={
        "job_id": job.id, "company_id": cos["upseller"], "phase": "negotiated",
        "total": matched, "binding": True, "deposit": 100,
        "line_items": [li("labor+truck, add-ons removed", "base", matched, "base"),
                       li("price match vs Summit & Sons", "other", -(quotes["upseller"]["total"] - matched), "discount")],
        "verbatim_evidence": "Fine — I'll do seventeen-sixty-five, binding, because we want the business.",
    }).raise_for_status()

    # --- report ------------------------------------------------------------
    rep = c.get(f"/api/jobs/{job.id}/report", headers=h).json()
    ranked = [(r["company"]["name"], r.get("final_total"), r["score"]) for r in rep["ranking"]]
    print("\nranking:")
    for name, total, score in ranked:
        print(f"  {score:>6}  {name:<26} ${total}")
    assert ranked[0][0] == "Premier Coast Van Lines", "negotiated price-match should win"
    assert ranked[-1][0] == "QuickBudget Movers", "cheapest-but-flagged should rank last"
    winner = rep["ranking"][0]
    assert winner["saved_in_negotiation"] > 0
    print(f"\nsaved in negotiation: ${winner['saved_in_negotiation']}")
    print(f"recommendation: {rep['recommendation']}")
    print("\nSMOKE TEST PASSED")


if __name__ == "__main__":
    main()
