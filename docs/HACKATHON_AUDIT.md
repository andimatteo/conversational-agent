# Hackathon compliance audit

Audit date: 2026-07-18. This file distinguishes implemented safeguards from
evidence that still has to be produced in the live rehearsal. Transcript-only
debug runs are useful product tests, but **do not count as live voice calls** for
the ElevenLabs challenge.

| Challenge criterion | Current state | Evidence / remaining action |
|---|---|---|
| Voice intake with ElevenLabs | Implemented, live proof pending | Browser signed session: `POST /api/jobs/:id/voice-session`; Estimator prompt is generated from the domain sheet. Record one complete browser intake in the final rehearsal. |
| At least one document type into the same spec | Implemented and tested | PDF/image/text parser merges into `job.spec`; extracted fields now pass the same domain-schema validator as form/voice data. |
| User-confirmed spec reused verbatim | Implemented and tested | Calls are locked before confirmation. Each run freezes the spec and stores a SHA-256 `spec_hash` on every call; all calls in the run use that copy. |
| Real-world market source | Implemented and tested offline | Google Places/Yelp/OSM discovery is normalized. The scheduler promotes every callable item backed by Google Places, preserving its real name, phone and place id. Run one live Google scan in the demo area. |
| Batch/parallel quote gathering | Implemented and tested | For `n` vendors, concurrency is `ceil(sqrt(n))`; e.g. 10 becomes 4/4/2. A hard barrier waits for every terminal result before the next batch. |
| Consistent knowledge between calls | Implemented and tested | Every batch gets one frozen context. Same-batch results are invisible; completed results appear only in the next knowledge version. |
| Three distinct negotiation styles | Configured; live proof pending | Stonewaller, lowballer and upseller policies exist per domain. Debug covers all three, but the qualifying demo still needs three live voice conversations (counter-agents or the allow-listed human phone). |
| Itemised comparable quotes | Implemented and tested | Canonical fee taxonomy, structured quote model, red-flag engine and report. Live rehearsal must confirm all three agents actually invoke the logging tool. |
| Negotiation moves price/terms using leverage | Grounded implementation; live proof pending | Closer receives own history plus exact allowed competing quote ids from a frozen DB snapshot. Debug test proves a concession cannot occur without both. Record one live concession with initial/final totals. |
| Honest AI disclosure | Prompt-enforced; live eval pending | Every domain has an explicit disclosure and robot-question answer. Play at least one “are you a robot?” exchange in the final demo. |
| No invented spec or bid | Architecturally constrained and tested | `get_call_context` is the frozen truth source; only completed, evidence-verified and post-call-grounded quotes enter leverage. The semantic validator checks spoken dollar amounts, competitor mentions and declared quote IDs against that snapshot; ungrounded results cannot become the best offer or recommendation. |
| Friction handling | Implemented; live proof pending | Prompts cover interruption, vague quotes, callbacks, refusal and hangup; audio bridge supports barge-in. Rehearse at least one refusal/interruption. |
| Structured outcome for every call | Enforced | Tool validation requires quote/callback/decline details. Finalization infers and visibly marks a failed/hangup outcome if the agent omits its tool, so no terminal call remains vague. |
| Learn from every completed vendor call | Implemented and tested | A mandatory backend finalization pass extracts price-relevant questions, upserts them by domain+area with call/vendor provenance, and feeds future intake forms. |
| Recall vendors | Implemented and tested | Batch completion produces explainable follow-up recommendations; selected vendors can be recalled with exact DB leverage. An atomic persistent guard permits at most two callbacks per job/vendor, including reserved, failed and completed attempts, preventing loops and harassment. |
| Ranked report with transcript evidence | Implemented and tested | Risk-aware ranking, fee detail, evidence verification status, call id, conversation id and authenticated audio URL. Debug evidence is explicitly labelled synthetic and has no audio. |
| Twilio + ElevenLabs live phone demo | Code complete; external setup pending | `POST /api/jobs/:jobId/calls/demo {company_id, phase}` submits a native one-recipient batch to only `DEMO_PHONE_NUMBER`, preserves the selected Google vendor, reconciles terminal recipient state, and fetches transcript/audio. No Twilio number is currently imported in ElevenLabs, so `ELEVENLABS_PHONE_NUMBER_ID` still has to be configured. |
| Closed-loop live demonstration | Not yet proven | Final acceptance requires one recorded run: voice+document intake → confirm → three live styles → at least one grounded concession → report with playable evidence. |

## Safety distinction

- `DEBUG_CALLS=true`: real Google vendor identity, generated structured
  transcript, no phone, no counter-agent, no ElevenLabs session, no audio.
- Bulk mode with `DEBUG_CALLS=false`: real vendor phones are sent through
  ElevenLabs native Twilio batches only when the independent
  `LIVE_VENDOR_CALLS_ENABLED=true` gate is also set; use only after legal/compliance review.
- Explicit live demo: always calls the single server-side allow-listed human
  number. The client cannot submit an arbitrary destination.

## Automated evidence

Run all offline checks with:

```bash
make test
```

The batching regression specifically proves 10 vendors → 4/4/2, frozen
same-batch knowledge, all-vendor completion, learned-question persistence,
realtime summary fields, semantic anti-hallucination validation, atomic/idempotent run
ownership, provider failure reconciliation, and rejection of a third vendor recall.
