# Demo run-of-show: safe scale preview + qualifying live proof

The demo has two deliberately separate tracks. Never describe debug transcripts as
voice calls.

- **Track A — DEBUG / transcript-only:** shows real Google vendor discovery, all-vendor
  coverage, deterministic quote extraction, sqrt batching, knowledge barriers,
  learning, follow-ups, and reporting. It makes no phone call and creates no audio.
- **Track B — LIVE qualifying demo:** ElevenLabs Caller/Closer reaches the one
  allow-listed human phone through the imported Twilio number. This is the track that
  can satisfy the challenge's live-conversation requirement.

Use different jobs for the two tracks so synthetic debug quotes never become leverage
in the qualifying live negotiation.

## Verification status before rehearsal

Verified offline today:

- [x] Debug calls preserve real Google vendor identities but use no telephony,
  ElevenLabs conversation, counter-agent, or audio.
- [x] All eligible Google vendors are scheduled by default.
- [x] `n` vendors use batches of `ceil(sqrt(n))`; 10 vendors execute as 4/4/2.
- [x] A batch closes only after every member is terminal; peer calls share one frozen
  snapshot and see no sibling result early.
- [x] Every terminal call runs the learned-question pass and refreshes explainable
  follow-up recommendations.
- [x] Debug negotiation cites exact frozen quote IDs and only moves price when verified
  leverage exists.
- [x] A vendor can be recalled at most twice; the third attempt is rejected even under
  concurrent or repeated requests.
- [x] Only post-call grounded claims can enter the best-offer panel or recommendation.

Still to prove live—leave these unchecked until the rehearsal artifacts exist:

- [ ] `/calls/demo` successfully rings the configured human phone through
  Twilio + ElevenLabs and finalizes transcript plus MP3.
- [ ] Three distinct live quote conversations are completed against three role-play
  styles: stonewaller, lowballer, and upseller.
- [ ] All three live calls end in structured outcomes with itemised comparable quotes
  or an explicit callback/decline.
- [ ] At least one live call includes an honest AI-disclosure/“are you a robot?” moment.
- [ ] One live follow-up produces a measurable price or terms concession because of an
  exact competing quote gathered in the earlier live calls.
- [ ] The concession sentence is verified in the live transcript and its recording is
  playable through the authenticated audio endpoint.

Passing the offline debug tests does not check any box in the second list.

## Preflight

1. Keep `DEBUG_CALLS=true`; bulk discovery remains safe and `/calls/demo` still works as
   the explicit exception.
2. Set `DEMO_PHONE_NUMBER` to the authorized role-player's E.164 number in `.env`.
3. Set `ELEVENLABS_PHONE_NUMBER_ID` to the Twilio number imported in ElevenLabs.
4. Confirm `ELEVENLABS_API_KEY`, `AGENT_TOOL_SECRET`, `PUBLIC_BASE_URL`, and the public
   webhook route. Leave `LIVE_VENDOR_CALLS_ENABLED=false` for the allow-listed demo.
5. Restart FastAPI after `.env` changes and run `python -m agents.provision` after any
   prompt/tool/tunnel change.
6. Check `GET /api/runtime-config`: `debug_mode=true`,
   `demo_phone_configured=true`, and `twilio_number_configured=true`.
7. Create one confirmed **debug job** and a separate confirmed **live job**, both with
   at least three Google vendors attached.

## 0. Hook — 20 seconds

“The real price is already in the market, but it is hidden behind phone calls nobody
has time to make. QuoteWise builds one confirmed scope, asks every vendor the same
questions, and negotiates without inventing leverage.”

## 1. Intake — two doors, one confirmed spec — 50 seconds

- Show the ElevenLabs Estimator interview asking a professional, domain-specific
  question.
- Upload a supported document and show extracted fields entering the same JSON spec.
- Confirm the spec. Point out that edits reset confirmation and that the spec is hashed
  and frozen when a call run begins.

Criterion shown: voice/document intake converge on one user-confirmed specification.

## 2. Track A: real market, safe all-vendor scale — 75 seconds

On the **debug job**:

1. Show the Google Places call list and promote it with
   `POST /companies/from-call-list {}`. Explain that every callable Google vendor is
   included; no top-three sampling occurs.
2. Show the global debug banner from `/api/runtime-config`: real vendor identity,
   synthetic labelled transcript, no dial, no ElevenLabs session, no audio.
3. Start `POST /calls/start {"phase":"quote"}`.
4. In the Calls header, show in real time:
   - current risk-adjusted best offer;
   - observed offer range;
   - called/total;
   - active batch and knowledge version.
5. Explain the scheduler with the actual count: batch size is `ceil(sqrt(n))`. Calls in
   one batch run concurrently, but the next batch cannot start until all are terminal.
   Every peer receives the same frozen snapshot; only the next batch learns the newly
   completed results.
6. Open a transcript and its structured quote. Explicitly point to
   `transcript_kind=debug_generated`; do not play or imply audio.
7. Show a newly learned customer question and its call/company provenance. The backend
   performs this pass even when the conversational agent forgets the tool.
8. Open `/follow-ups`: reasons and source quote IDs are visible, but recommendations do
   not dial automatically.

This track proves safe orchestration and grounding, not the voice criterion.

## 3. Track B: three qualifying live calls — 2 minutes

Switch to the clean **live job**. The same authorized human answers every phone call but
role-plays three distinct vendors; the selected Google records provide distinct vendor
identities. No discovered business is dialled.

For each selected `company_id`, invoke:

```http
POST /api/jobs/{job_id}/calls/demo
{"company_id":"co_selected","phase":"quote"}
```

Wait for each call to become terminal before starting the next demo beat.
The endpoint uses a native one-recipient ElevenLabs batch; the destination remains
server-side and a confirmed terminal provider state is required before knowledge moves.

1. **Stonewaller:** interrupt, initially refuse a phone quote, then give a structured
   result only after hearing the complete confirmed scope.
2. **Lowballer:** give a cheap anchor, evade itemisation, then reveal contingent fees
   when the agent asks specifically.
3. **Upseller:** bundle an unnecessary add-on and use pressure; let the agent separate
   it from the comparable base scope.

During at least one call, ask “Am I talking to a robot?” The agent must disclose
honestly and return to the quote. After each call, verify outcome, line items,
transcript, `has_audio=true`, and authenticated `audio_url`.

Do not say these three styles have been demonstrated until all three live artifacts are
present. They are not yet live-proven in the repository state documented today.

## 4. The qualifying live negotiation — 60 seconds

Choose the live vendor whose earlier offer can credibly move. Only after the three live
quote calls have finalized, invoke:

```http
POST /api/jobs/{job_id}/calls/demo
{"company_id":"co_target","phase":"negotiate"}
```

The Closer receives a frozen context containing:

- the target vendor's own verified quote history;
- exact competing `quote_id`, company, total, and binding status;
- the unchanged spec hash and benchmark.

The role-player grants a concession only after the Closer cites an exact lower live bid.
State the initial and final total or changed term, then play the concession sentence from
`GET /api/jobs/{job_id}/calls/{call_id}/audio`. In the report, show that the negotiated
evidence is marked verified in transcript and cites the call/quote IDs.

If the price or terms do not move, the challenge criterion is not met: do not substitute
the deterministic debug concession or claim a successful negotiation.
No vendor can be recalled more than twice; if both slots are consumed, use the existing
evidence rather than attempting another call.

## 5. Report and close — 45 seconds

- Show the ranked recommendation, itemised totals, initial → negotiated delta, binding
  status, red flags, and explanation of why a suspicious cheap offer can rank lower.
- Open evidence for both a debug row and a live row: debug is labelled and has no audio;
  live has a verified transcript citation and playable recording.
- Show a structured non-quote outcome if available: callback commitment, decline, or
  hangup—not a vague summary.
- Close on `verticals/plumbing.yaml`: changing market behavior is configuration, while
  batching, grounding, honesty, learning, and evidence stay the same.

## Failure modes to rehearse

- **Demo phone does not ring:** verify both phone settings, ElevenLabs phone import,
  tunnel reachability, and re-provisioned webhook URLs. Do not fall back to claiming a
  debug transcript was live.
- **Call finalizes without audio:** keep the transcript/outcome, state the failure
  honestly, and leave the audio proof box unchecked.
- **No concession:** run a fresh live negotiation only when a lower verified live quote
  exists; never invent or use a debug bid on the qualifying job.
- **Agent omits outcome or learning tool:** the finalizer supplies a structured fallback
  outcome and always runs the backend learning pass; disclose that it was inferred.
- **Batch stalls:** inspect `call_runs`, `call_batches`, recipient status, and configured
  timeout. Never advance the knowledge version manually.
- **Tunnel changed:** update `PUBLIC_BASE_URL`, restart the API, and re-run provisioning.
