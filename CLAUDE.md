# CLAUDE.md — session handoff for QuoteWise

Claude Code loads this file automatically. Keep it aligned with implementation and with
the proof status in `docs/DEMO_SCRIPT.md`.

## Product and fixed decisions

QuoteWise targets the ElevenLabs **The Negotiator** challenge: one confirmed job spec,
comparable market calls, grounded negotiation, and a ranked evidence-backed report.

Decisions current as of 2026-07-19:

- Domain behavior is configuration in `verticals/<domain>[-<area>].yaml`. The MVP is
  **residential plumbing / 28202**; moving remains the second proof-of-swap sheet.
- Market discovery uses Google Places + Yelp + OSM, but the callable scheduler promotes
  every eligible **Google Places** vendor by default.
- `DEBUG_CALLS=true` is the safe global default for bulk work: real Google identities,
  deterministic synthetic transcripts and structured results, no phone call,
  ElevenLabs conversation, counter-agent, or audio.
- `DEBUG_CALLS=false` is still not authority to dial real businesses:
  `LIVE_VENDOR_CALLS_ENABLED=true` is a separate operator gate.
- The resettable hackathon path is one explicit hybrid job. `/calls/start` keeps every
  Google identity in the logical batch run, routes only one preselected final-batch
  identity to the allow-listed `DEMO_PHONE_NUMBER`, and leaves every other row
  transcript-only. `/calls/demo` then calls the same human back for negotiation.
- Never use a debug transcript as proof of a live voice criterion or imply a simulated
  offer came from the named Google business. In the disclosed role-play it may be used
  only as exact “simulated demo-market” leverage.
- Stack: FastAPI/SQLite backend plus external Lovable frontend.

## Architecture

`negotiator/packs.py` loads and validates domain sheets. Intake arrives through the
ElevenLabs Estimator, schema-driven web form, or document parser and converges on
`job.spec`; any edit resets confirmation. `negotiator/spec_validation.py` performs deep
type/enum/nested validation before confirmation.

Discovery records remain real. `negotiator/callrunner.py` idempotently promotes all
callable Google Places leads, freezes the confirmed spec for the run, computes
`ceil(sqrt(n))`, and partitions vendors into synchronous-barrier batches. Calls inside a
batch run concurrently. All receive one immutable context from
`negotiator/knowledge.py`; results become visible only after every member is terminal
and the next knowledge version opens. Context separates the vendor's own quote history
from exact allowed competitive claims.

Mode selection:

- debug on → `negotiator/debugcalls.py`, transcript-only;
- debug off + Google vendor → ElevenLabs native Twilio batch calling;
- simulated company → live ElevenLabs agent-to-agent bridge;
- prepared hybrid `/calls/start` → preselected human explorer in quote batch one,
  N-1 debug transcripts, then an automatic grounded callback after the full barrier;
- prepared `/calls/demo phase=negotiate` → legacy explicit rehearsal endpoint only.

The Caller and Closer log quote/outcome by exact `call_id`. Negotiated quotes carry
`leverage_quote_ids`, checked against that call's frozen allowed claims. Voice
finalization fetches ElevenLabs transcript/audio, verifies verbatim evidence, infers a
structured outcome if the agent omitted it, and **always** runs the backend learning
pass. `negotiator/learnings.py` extracts customer questions from contingent items,
conditions, and vendor speech, deduplicates per `(vertical, area_code)`, and records
`times_seen`, source calls, and companies. After every barrier,
`knowledge.follow_up_plan` writes explainable recall recommendations; it never dials by
itself.
`negotiator/recall_limits.py` atomically consumes at most two recall slots per
job/vendor. Reserved, running, failed, and completed attempts all count; configuration
may lower this limit but cannot raise it above two.

The Calls board polls `/call-queue` for risk-adjusted best offer, offer range,
called/total, batch status, knowledge version, and follow-up annotations. Recordings are
exposed only through the owner-scoped
`GET /api/jobs/{job_id}/calls/{call_id}/audio`; debug calls correctly return 404.

## Current state — 2026-07-19

Verified offline:

- `tests.debugcalls_test`: deterministic quote transcripts for three style policies,
  no network/ElevenLabs/audio, real vendor records unchanged, evidence included in the
  generated transcript, grounded negotiation only with exact own/competing history.
- `tests.batching_test`: 10 vendors → batches 4/4/2, hard barrier, knowledge versions
  0/1/2, no within-batch leakage, all-vendor completion, live queue summary, mandatory
  learning marker, and grounded single-vendor recall.
- `tests.demo_campaign_test`: 9 transcript-only vendors + one allow-listed role-player
  in the final 4/4/2 batch, preserved Google identity, disclosed exact simulated
  leverage, verified concession fixture, and rejection of a third recall.
- `tests.demo_reset_test`: repeated non-destructive reset, preservation of calls,
  quotes, runs, batches, claims, recalls and recordings, plus active-work refusal.
- `tests.learnings_test`: normalized upsert/dedup, idempotent call replay, provenance,
  concurrent completion safety, area isolation, transcript/condition/contingent
  extraction, and generic fallback.
- `tests.spec_validation_test`: confirmation rejects malformed schema values and accepts
  a valid domain spec.
- Existing offline suites cover red flags/ranking (`smoke_test`), estimator and learned
  intake (`estimator_test`), documents (`documents_test`), discovery
  (`market_discovery_test`), queue guards (`callqueue_test`), and auth (`auth_test`).
- Document text parsing has previously been exercised against OpenAI; PDF/photo paths
  remain dependent on live service behavior.
- One earlier agent-to-agent plumbing call produced transcript and recording, but this
  is not evidence of three live styles or the Twilio demo requirement.

Implemented but **not yet live-proven**:

1. The integrated `/calls/start` final-batch Twilio call reaching the configured human
   and finalizing its initial transcript/MP3.
2. The `/calls/demo phase=negotiate` callback measurably moving price or terms using an
   exact, explicitly disclosed simulated demo-market offer from the completed run.
3. Three distinct live role-play negotiation-style artifacts; the one-human hybrid
   run alone does not prove this criterion.
4. End-to-end Lovable playback through the authenticated audio endpoint.
5. Bulk `DEBUG_CALLS=false` against Google vendor phone numbers. Do not exercise this
   without explicit authorization; it is not needed for the allow-listed demo.

Do not change any item above to “live tested” until the corresponding call records,
transcripts, and recordings exist. The judge-facing checkbox is in
`docs/DEMO_SCRIPT.md`.

## Runtime/API contract

- `GET /api/runtime-config` → debug behavior plus masked demo/Twilio readiness.
- `POST /api/jobs/{id}/call-list/discover` and `GET .../call-list` → discovery.
- `POST /api/jobs/{id}/companies/from-call-list {}` → promote all callable Google
  vendors. `count=0` means all; positive count exists only for old/diagnostic clients.
- `POST /api/jobs/{id}/calls/start {phase, company_ids?, retry_completed?,
  recommended_only?, idempotency_key?}` → atomic/idempotent background run.
  `parallel` is deprecated and ignored. On a prepared demo this is quote-only and
  includes all vendors; the selected human explores in quote batch one and is
  automatically recalled only after the final quote barrier.
- `GET /api/jobs/{id}/call-queue` → live summary, batches, rows, and follow-ups.
- `GET /api/jobs/{id}/follow-ups` → recommendations and source quote IDs; no auto-dial.
- `POST /api/jobs/{id}/calls/demo {company_id, phase}` → only the configured demo
  destination; no `to_number` in the request. Prepared demos accept only their fixed
  target and negotiation phase after the complete quote barrier.
- `GET /api/jobs/{id}/calls` → transcripts plus `has_audio`/`audio_url`, never raw paths.
- `GET /api/jobs/{id}/calls/{call_id}/audio` → authenticated MP3 or 404.
- `GET /api/jobs/{id}/report` → ranking with quote/call IDs, evidence verification kind,
  and audio URL when present.

Every `/api` job route is bearer-authenticated and owner-scoped. `/agent-tools/*`
is machine-to-machine and requires the provisioned `X-QuoteWise-Tool-Key` secret;
late writes to terminal calls are rejected.

## Environment and provisioning

Required/important `.env` values:

```dotenv
VERTICAL=plumbing
DEBUG_CALLS=true
LIVE_VENDOR_CALLS_ENABLED=false
ELEVENLABS_API_KEY=
OPENAI_API_KEY=
GOOGLE_PLACES_API_KEY=
YELP_API_KEY=
PUBLIC_BASE_URL=https://YOUR-PUBLIC-TUNNEL.example
AGENT_TOOL_SECRET=
ELEVENLABS_PHONE_NUMBER_ID=
DEMO_PHONE_NUMBER=
CALL_BATCH_TIMEOUT_SECS=900
CALL_POLL_INTERVAL_SECS=2
CALL_RUN_LEASE_SECS=2100
MAX_VENDOR_RECALLS=2
```

Environment is loaded at import time: restart FastAPI after changing it. Re-run
`python -m agents.provision` after changing prompts, tool schemas, a vertical sheet, or
`PUBLIC_BASE_URL`. Never commit `.env`, the demo number, API keys, or
`agents/registry.json`.

## Commands

```bash
source .venv/bin/activate

# Offline verification
make test

# Focused suites
python -m tests.debugcalls_test
python -m tests.batching_test
python -m tests.learnings_test
python -m tests.spec_validation_test
python -m tests.smoke_test
python -m tests.estimator_test
python -m tests.documents_test
python -m tests.market_discovery_test
python -m tests.callqueue_test
python -m tests.auth_test
python -m tests.runclaims_test
python -m tests.recall_limits_test
python -m tests.evidence_test
python -m tests.provider_status_test

# Runtime
uvicorn negotiator.server:app --port 8000
python -m agents.provision
python -m negotiator.demo_reset                # archive prior demo, create a fresh confirmed job + promoted vendors
python -m negotiator.seed --with-sample-spec
python -m simulation.run_intake --job job_X
curl localhost:8000/api/jobs/job_X/report | python -m json.tool
```

## Operational gotchas

- Bulk scheduling with no `company_ids` means every eligible Google vendor. A second
  quote run skips completed vendors unless `retry_completed=true`.
- Do not calculate concurrency in the UI; the backend always owns `ceil(sqrt(n))`.
- The spec is frozen for the entire run; quote knowledge changes only between batches.
- Follow-up recommendations are plans, not actions. Use `recommended_only=true` or
  explicit company IDs to authorize recalls.
- At most two recalls are ever reserved per job/vendor. A failed or queued attempt
  still consumes a slot; the third request must remain rejected.
- `DEBUG_CALLS=true` suppresses ordinary bulk telephony. A job explicitly prepared by
  `demo_reset` is the narrow exception: its one fixed target goes to the configured
  human in quote batch one and once after the final quote barrier; all other
  destinations remain suppressed.
- Debug transcripts must remain visibly labelled `debug_generated`; they have no
  conversation ID or recording.
- Audio playback requires the same Bearer token as the Calls API.
- Learned questions persist in `data/negotiator.db` per vertical+area and disappear if
  the database is wiped.
- Re-provision after tunnel churn; otherwise ElevenLabs tools call a stale URL.
- Dynamic variables include job, company, call, batch, phase, and knowledge version.
- Numbers sanity anchor: plumbing water-heater test spec has fair range approximately
  $1,792–$4,257 and median $2,688; moving sample median is approximately $1,935.
- Repository origin: https://github.com/andimatteo/conversational-agent
