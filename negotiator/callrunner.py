"""Server-side call orchestration — the call queue resolves progressively.

The frontend presses "Start calls"; this module runs the agent-to-agent bridge
calls in background threads (same bridge as the CLI) and the queue endpoint
derives a live per-company status from the DB:

  to_call -> queued -> calling -> quote | callback | decline | hangup

Statuses come from real records (calls started/ended, outcomes logged by the
agents via webhooks), never from a script.
"""
import threading

from . import db
from .config import ELEVENLABS_API_KEY, registry_path

# One orchestration at a time per job. In-memory is fine: single-process demo.
_runs: dict[str, threading.Thread] = {}
_queued: dict[str, set] = {}   # job_id -> company_ids waiting for their turn


def queue_state(job_id: str) -> dict:
    """Live queue view: one row per company with status + running totals."""
    calls = db.where("calls", job_id=job_id)
    quotes = db.where("quotes", job_id=job_id)
    rows = []
    for co in db.where("companies", job_id=job_id):
        co_calls = sorted((c for c in calls if c["company_id"] == co["id"]),
                          key=lambda c: c.get("started_at", ""))
        latest = co_calls[-1] if co_calls else None
        co_quotes = sorted((q for q in quotes if q["company_id"] == co["id"]),
                           key=lambda q: q.get("created_at", ""))
        # latest of each phase: a caller may refine its quote mid-call (e.g. the
        # lowball anchor first, then the real all-in once fees are surfaced)
        initial = next((q for q in reversed(co_quotes) if q["phase"] == "initial"), None)
        negotiated = next((q for q in reversed(co_quotes) if q["phase"] == "negotiated"), None)

        if latest and not latest.get("ended_at"):
            status = "calling"
        elif latest and latest.get("ended_at"):
            status = latest.get("outcome") or "done"
        elif co["id"] in _queued.get(job_id, set()):
            status = "queued"
        else:
            status = "to_call"

        rows.append({
            "company": {"id": co["id"], "name": co["name"], "persona": co.get("persona", ""),
                        "source": co.get("source", "")},
            "status": status,
            "last_call_kind": (latest or {}).get("kind", ""),
            "conversation_id": (latest or {}).get("conversation_id", ""),
            "initial_total": initial["total"] if initial else None,
            "negotiated_total": negotiated["total"] if negotiated else None,
            "red_flags": (negotiated or initial or {}).get("red_flags", []),
        })
    running = job_id in _runs and _runs[job_id].is_alive()
    return {"running": running, "queue": rows}


def start_calls(job_id: str, phase: str, company_ids: list[str] | None = None,
                parallel: bool = False) -> dict:
    """Kick off quote/negotiate calls in the background; returns immediately."""
    import json
    if not ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY missing — calls are disabled.")
    if not registry_path().exists():
        raise RuntimeError("Agents not provisioned — run `python -m agents.provision`.")
    if job_id in _runs and _runs[job_id].is_alive():
        raise RuntimeError("Calls already running for this job.")

    reg = json.loads(registry_path().read_text())
    our_agent = reg["agents"]["caller" if phase == "quote" else "closer"]

    companies = [c for c in db.where("companies", job_id=job_id) if c.get("agent_id")]
    if company_ids:
        companies = [c for c in companies if c["id"] in company_ids]
    if phase == "negotiate":
        quoted = {q["company_id"] for q in db.where("quotes", job_id=job_id)}
        companies = [c for c in companies if c["id"] in quoted]
    if not companies:
        raise LookupError("No eligible companies (seed the simulated market first"
                          + (", gather quotes before negotiating" if phase == "negotiate" else "") + ").")

    _queued[job_id] = {c["id"] for c in companies}

    def _one(client, co):
        from simulation.run_calls import run_bridge_call
        _queued.get(job_id, set()).discard(co["id"])
        try:
            run_bridge_call(client, our_agent, co["agent_id"], job_id, co, phase, listen=False)
        except Exception as e:  # a failed dial must not kill the rest of the queue
            print(f"[callrunner] {co['name']}: {type(e).__name__}: {e}")

    def _run():
        from elevenlabs.client import ElevenLabs
        client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        if parallel:
            threads = [threading.Thread(target=_one, args=(client, co), daemon=True)
                       for co in companies]
            [t.start() for t in threads]
            [t.join() for t in threads]
        else:
            for co in companies:
                _one(client, co)
        _queued.pop(job_id, None)

    t = threading.Thread(target=_run, daemon=True)
    _runs[job_id] = t
    t.start()
    return {"started": True, "phase": phase, "parallel": parallel,
            "companies": [{"id": c["id"], "name": c["name"]} for c in companies]}
