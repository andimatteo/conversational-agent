"""Voice intake: talk to The Estimator through your mic/speakers.
It interviews you like a professional estimator and saves the structured
spec via the save_job_spec webhook (server must be running).

  python -m simulation.run_intake --job job_xxxx
"""
import argparse
import json
import sys

from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import Conversation, ConversationInitiationData
from elevenlabs.conversational_ai.default_audio_interface import DefaultAudioInterface

from negotiator import db
from negotiator.config import ELEVENLABS_API_KEY, registry_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    args = ap.parse_args()
    if not db.get("jobs", args.job):
        sys.exit(f"Unknown job {args.job} — run `python -m negotiator.seed` first.")

    reg = json.loads(registry_path().read_text())
    conv = Conversation(
        ElevenLabs(api_key=ELEVENLABS_API_KEY), reg["agents"]["estimator"],
        requires_auth=True, audio_interface=DefaultAudioInterface(),
        config=ConversationInitiationData(dynamic_variables={"job_id": args.job}),
        callback_agent_response=lambda t: print(f"  [ESTIMATOR] {t}"),
        callback_user_transcript=lambda t: print(f"  [YOU] {t}"),
    )
    print("=== Voice intake — answer into your mic. Ctrl+C to hang up. ===")
    conv.start_session()
    try:
        conv.wait_for_session_end()
    except KeyboardInterrupt:
        conv.end_session()

    job = db.get("jobs", args.job)
    print("\nSpec now on file:")
    print(json.dumps(job["spec"], indent=2))
    print(f"\nConfirm it (required before any call):\n  curl -X POST http://localhost:8000/api/jobs/{args.job}/confirm")


if __name__ == "__main__":
    main()
