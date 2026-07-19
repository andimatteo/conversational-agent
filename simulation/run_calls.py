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

from negotiator import config, db
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


def run_bridge_call(client, our_agent_id, cp_agent_id, job_id, company, kind, listen,
                    call_id=None, call_context=None):
    """Run a voice bridge call.

    The batch scheduler may pre-create ``call_id`` with a frozen knowledge
    snapshot.  CLI callers omit it and get a standalone record as before.
    """
    if config.DEBUG_CALLS:
        raise RuntimeError(
            "DEBUG_CALLS=true forbids agent-to-agent sessions and audio; "
            "use the server batch scheduler to generate transcript-only calls."
        )
    call_id = call_id or db.new_id("call")
    call = db.get("calls", call_id) or {
        "id": call_id, "job_id": job_id, "company_id": company["id"],
        "kind": kind, "created_at": now(),
    }
    _reserve_standalone_recall(call, kind)
    if call_context and not call.get("knowledge_snapshot"):
        call["knowledge_snapshot"] = call_context
    call.update({"started_at": now(), "status": "calling", "mode": "agent_bridge"})
    db.put("calls", call_id, call, job_id=job_id, company_id=company["id"])

    dyn = {"job_id": job_id, "company_id": company["id"], "company_name": company["name"],
           "call_id": call_id, "batch_id": call.get("batch_id", "standalone"),
           "phase": kind, "knowledge_version": call.get("knowledge_version", 0)}
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

    conv_id = getattr(us, "_conversation_id", None) or getattr(us, "conversation_id", "") or ""
    _finalize_call(call_id, job_id, company["id"], conv_id)
    return call_id


def run_human_call(client, our_agent_id, job_id, kind):
    """YOU are the counterparty: the negotiator speaks through your speakers,
    you answer into the mic — the live-demo 'human on the line' mode."""
    if config.DEBUG_CALLS:
        raise RuntimeError(
            "DEBUG_CALLS=true forbids microphone/voice sessions; use the explicit phone demo endpoint."
        )
    from elevenlabs.conversational_ai.default_audio_interface import DefaultAudioInterface
    company = {"id": "co_human", "name": "Human Counterpart (live)", "persona": "", "source": "human"}
    db.put("companies", company["id"], {**company, "phone": "", "agent_id": ""}, job_id=job_id)
    call_id = db.new_id("call")
    call = {"id": call_id, "job_id": job_id, "company_id": company["id"],
            "kind": kind, "created_at": now(), "started_at": now(),
            "status": "calling", "mode": "human_microphone"}
    _reserve_standalone_recall(call, kind)
    db.put("calls", call_id, call,
           job_id=job_id, company_id=company["id"])

    conv = make_conversation(client, our_agent_id, DefaultAudioInterface(),
                             {"job_id": job_id, "company_id": company["id"],
                              "company_name": company["name"], "call_id": call_id,
                              "batch_id": "standalone", "phase": kind,
                              "knowledge_version": 0}, "NEGOTIATOR")
    print("\n=== HUMAN MODE — answer the phone into your mic (say 'hello?' to start) ===")
    conv.start_session()
    conv_id = conv.wait_for_session_end()
    _finalize_call(call_id, job_id, company["id"], conv_id or "")


def _reserve_standalone_recall(call: dict, kind: str) -> None:
    """Apply the same hard callback cap to legacy CLI/audio entry points."""
    if call.get("recall_reservation_id"):
        return
    prior = [row for row in db.where(
        "calls", job_id=call["job_id"], company_id=call["company_id"]
    ) if row.get("id") != call["id"]]
    if kind != "negotiate" and not prior:
        return
    from negotiator.recall_limits import reserve
    reservation_id = f"standalone:{call['id']}"
    slot = reserve(
        call["job_id"], call["company_id"], reservation_id,
        max_recalls=config.MAX_VENDOR_RECALLS, call_id=call["id"], status="calling",
        metadata={"mode": "legacy_cli", "phase": kind},
    )
    if slot is None:
        raise RuntimeError(
            f"This vendor reached the hard limit of {config.MAX_VENDOR_RECALLS} recalls."
        )
    call["recall_reservation_id"] = reservation_id
    call["recall_slot"] = slot


def _finalize_call(call_id, job_id, company_id, conv_id):
    call = db.get("calls", call_id)
    if not call:
        return
    call["conversation_id"] = conv_id or call.get("conversation_id", "")

    # Backfill conversation_id onto quotes logged during this call, then pull
    # the authoritative transcript + audio from ElevenLabs.
    call_quotes = []
    for q in db.where("quotes", job_id=job_id, company_id=company_id):
        # New calls correlate explicitly.  Legacy agents that omit call_id may
        # only attach to the latest active attempt, never to every old quote.
        if q.get("call_id") == call_id:
            if not q.get("conversation_id"):
                q["conversation_id"] = conv_id
            call_quotes.append(q)
            db.put("quotes", q["id"], q, job_id=job_id, company_id=company_id, phase=q["phase"])
    if conv_id:
        try:
            _fetch_conversation(call, conv_id)
        except Exception as exc:
            # Transcript/audio retrieval is useful evidence but must never
            # leave a zombie call in status=calling. Persist the technical
            # problem and finish the structured terminal path below.
            call.setdefault("artifact_errors", []).append(f"{type(exc).__name__}: {exc}"[:300])
    from negotiator.evidence import (validate_call_grounding,
                                     verify_quote_counterparty_evidence)
    for q in call_quotes:
        evidence_check = verify_quote_counterparty_evidence(call, q)
        q["counterparty_evidence"] = evidence_check
        # A negotiator repeating its own tool payload is never evidence. Only
        # explicit vendor/user turns can verify the quote and each item amount.
        q["evidence_verified"] = bool(evidence_check.get("valid"))
        q["evidence_kind"] = "voice_transcript"
        db.put("quotes", q["id"], q, job_id=job_id, company_id=company_id, phase=q["phase"])

    call["grounding_validation"] = validate_call_grounding(call, call_quotes)
    for q in call_quotes:
        q["grounding_verified"] = bool(call["grounding_validation"].get("valid"))
        db.put("quotes", q["id"], q, job_id=job_id, company_id=company_id, phase=q["phase"])

    # Structured outcome is an invariant, not an optional prompt courtesy.
    if not call.get("outcome"):
        call["outcome"] = "quote" if call_quotes else "hangup"
        call["summary"] = ("Quote captured; the agent omitted the outcome tool."
                           if call_quotes else
                           "Call ended before a structured quote, callback or decline was logged.")
        call["outcome_inferred"] = True
    call["status"] = "completed" if call.get("outcome") != "hangup" else "failed"
    call["transcript_kind"] = "elevenlabs_voice" if call.get("transcript") else "none"

    # Every terminal vendor call runs the same backend learning pass, even if
    # the conversational agent forgot to invoke its optional logging tool.
    try:
        from negotiator.learnings import persist_questions, questions_from_call
        job = db.get("jobs", job_id)
        quote = call_quotes[-1] if call_quotes else None
        questions = questions_from_call(job, call, quote)
        call["learning_analysis"] = persist_questions(
            job, questions, source_call_id=call_id, company_id=company_id)
    except Exception as exc:
        call["learning_analysis"] = {"logged": False, "error": str(exc)[:200]}
    call["ended_at"] = now()
    db.put("calls", call_id, call, job_id=job_id, company_id=company_id)
    if call.get("recall_reservation_id"):
        try:
            from negotiator.recall_limits import set_status
            set_status(job_id, company_id, call["recall_reservation_id"], call["status"])
        except Exception as exc:
            call.setdefault("audit_warnings", []).append(
                f"Recall reservation status update failed: {type(exc).__name__}: {exc}"[:300])
            db.put("calls", call_id, call, job_id=job_id, company_id=company_id)
    print(f"  call {call_id} finalized (conversation {conv_id or 'n/a'}) "
          f"outcome={call.get('outcome')}")


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
