"""Call orchestrator: runs the Caller (quote phase) or Closer (negotiate phase)
against the simulated market — or against YOU (human-in-the-loop mode).

  python -m simulation.run_calls --job job_xxxx --phase quote               # all simulated companies
  python -m simulation.run_calls --job job_xxxx --phase negotiate           # companies with a quote
  python -m simulation.run_calls --job job_xxxx --phase quote --company co_x --listen
  python -m simulation.run_calls --job job_xxxx --phase quote --human       # YOU answer via mic
  ... add --parallel to run companies concurrently (batch calling, silent)

The API server must be running (webhook tools write into its DB).
"""
import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone

import httpx
from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import Conversation, ConversationInitiationData

from negotiator import db
from negotiator.config import ELEVENLABS_API_KEY, RECORDINGS_DIR, registry_path
from .bridge import BridgeAudioInterface, wire

MAX_CALL_SECS = 420
API = "https://api.elevenlabs.io/v1/convai"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_registry() -> dict:
    if not registry_path().exists():
        sys.exit("agents/registry.json missing — run `python -m agents.provision` first.")
    return json.loads(registry_path().read_text())


def make_conversation(client, agent_id, audio_interface, dyn: dict, label: str) -> Conversation:
    return Conversation(
        client, agent_id, requires_auth=True,
        audio_interface=audio_interface,
        config=ConversationInitiationData(dynamic_variables=dyn),
        callback_agent_response=lambda t: print(f"  [{label}] {t}"),
        callback_user_transcript=lambda t: print(f"  [{label} hears] {t}"),
    )


def run_bridge_call(client, our_agent_id, cp_agent_id, job_id, company, kind, listen):
    call_id = db.new_id("call")
    db.put("calls", call_id, {"id": call_id, "job_id": job_id, "company_id": company["id"],
                              "kind": kind, "started_at": now()},
           job_id=job_id, company_id=company["id"])

    dyn = {"job_id": job_id, "company_id": company["id"], "company_name": company["name"]}
    a_us = BridgeAudioInterface("negotiator", RECORDINGS_DIR / f"{call_id}_negotiator.wav", listen)
    a_cp = BridgeAudioInterface(company["name"], RECORDINGS_DIR / f"{call_id}_counterparty.wav", False)
    wire(a_us, a_cp)

    us = make_conversation(client, our_agent_id, a_us, dyn, "NEGOTIATOR")
    cp = make_conversation(client, cp_agent_id, a_cp, dyn, company["name"].upper())

    print(f"\n=== {kind.upper()} call -> {company['name']} ({company['persona']}) ===")
    cp.start_session()
    time.sleep(0.5)  # let the counterparty "pick up" first
    us.start_session()

    done = threading.Event()
    for conv in (us, cp):
        threading.Thread(target=lambda c=conv: (c.wait_for_session_end(), done.set()),
                         daemon=True).start()
    if not done.wait(timeout=MAX_CALL_SECS):
        print("  [watchdog] max call length reached, ending.")
    for conv in (us, cp):
        try:
            conv.end_session()
        except Exception:
            pass
    time.sleep(2)  # let sessions flush

    conv_id = getattr(us, "conversation_id", "") or ""
    _finalize_call(call_id, job_id, company["id"], conv_id)
    return call_id


def run_human_call(client, our_agent_id, job_id, kind):
    """YOU are the counterparty: the negotiator speaks through your speakers,
    you answer into the mic — the live-demo 'human on the line' mode."""
    from elevenlabs.conversational_ai.default_audio_interface import DefaultAudioInterface
    company = {"id": "co_human", "name": "Human Counterpart (live)", "persona": "", "source": "human"}
    db.put("companies", company["id"], {**company, "phone": "", "agent_id": ""}, job_id=job_id)
    call_id = db.new_id("call")
    db.put("calls", call_id, {"id": call_id, "job_id": job_id, "company_id": company["id"],
                              "kind": kind, "started_at": now()},
           job_id=job_id, company_id=company["id"])

    conv = make_conversation(client, our_agent_id, DefaultAudioInterface(),
                             {"job_id": job_id, "company_id": company["id"],
                              "company_name": company["name"]}, "NEGOTIATOR")
    print("\n=== HUMAN MODE — answer the phone into your mic (say 'hello?' to start) ===")
    conv.start_session()
    conv_id = conv.wait_for_session_end()
    _finalize_call(call_id, job_id, company["id"], conv_id or "")


def _finalize_call(call_id, job_id, company_id, conv_id):
    call = db.get("calls", call_id)
    call.update({"conversation_id": conv_id, "ended_at": now()})
    db.put("calls", call_id, call, job_id=job_id, company_id=company_id)

    # Backfill conversation_id onto quotes logged during this call, then pull
    # the authoritative transcript + audio from ElevenLabs.
    for q in db.where("quotes", job_id=job_id, company_id=company_id):
        if not q.get("conversation_id"):
            q["conversation_id"] = conv_id
            db.put("quotes", q["id"], q, job_id=job_id, company_id=company_id, phase=q["phase"])
    if conv_id:
        _fetch_conversation(call, conv_id)
        db.put("calls", call_id, call, job_id=job_id, company_id=company_id)
    print(f"  call {call_id} finalized (conversation {conv_id or 'n/a'}) "
          f"outcome={call.get('outcome', 'NOT LOGGED')}")


def _fetch_conversation(call: dict, conv_id: str):
    h = {"xi-api-key": ELEVENLABS_API_KEY}
    with httpx.Client(timeout=30) as c:
        for _ in range(6):  # transcript can lag a few seconds behind hangup
            r = c.get(f"{API}/conversations/{conv_id}", headers=h)
            if r.status_code == 200 and r.json().get("transcript"):
                call["transcript"] = [{"role": t.get("role"), "text": t.get("message")}
                                      for t in r.json()["transcript"] if t.get("message")]
                break
            time.sleep(3)
        r = c.get(f"{API}/conversations/{conv_id}/audio", headers=h)
        if r.status_code == 200:
            path = RECORDINGS_DIR / f"{conv_id}.mp3"
            path.write_bytes(r.content)
            call["audio_path"] = str(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--phase", choices=["quote", "negotiate"], default="quote")
    ap.add_argument("--company", help="single company id (default: all eligible)")
    ap.add_argument("--human", action="store_true", help="you answer via mic instead of a counter-agent")
    ap.add_argument("--listen", action="store_true", help="play the call on your speakers (needs pyaudio)")
    ap.add_argument("--parallel", action="store_true", help="run companies concurrently (silent)")
    args = ap.parse_args()

    job = db.get("jobs", args.job)
    if not job:
        sys.exit(f"Unknown job {args.job}. Run `python -m negotiator.seed` first.")
    if not job.get("confirmed"):
        sys.exit("Job spec is not confirmed — confirm it first (POST /api/jobs/{id}/confirm). "
                 "No calls are allowed before the user signs off on the spec.")

    reg = load_registry()
    client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
    our_agent = reg["agents"]["caller" if args.phase == "quote" else "closer"]

    if args.human:
        run_human_call(client, our_agent, args.job, args.phase)
        return

    companies = [c for c in db.where("companies", job_id=args.job) if c.get("agent_id")]
    if args.company:
        companies = [c for c in companies if c["id"] == args.company]
    if args.phase == "negotiate":
        quoted = {q["company_id"] for q in db.where("quotes", job_id=args.job)}
        companies = [c for c in companies if c["id"] in quoted]
    if not companies:
        sys.exit("No eligible companies (seed the market / gather quotes first).")

    if args.parallel and len(companies) > 1:
        threads = [threading.Thread(target=run_bridge_call,
                                    args=(client, our_agent, c["agent_id"], args.job, c, args.phase, False))
                   for c in companies]
        [t.start() for t in threads]
        [t.join() for t in threads]
    else:
        for c in companies:
            run_bridge_call(client, our_agent, c["agent_id"], args.job, c, args.phase, args.listen)

    print(f"\nDone. Report: GET /api/jobs/{args.job}/report")


if __name__ == "__main__":
    main()
