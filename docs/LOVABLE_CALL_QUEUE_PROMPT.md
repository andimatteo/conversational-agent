# Lovable prompt — batched live call queue + allow-listed phone demo

Paste the block below into the existing Lovable project as one message.
(Also update API_BASE if the backend tunnel has changed.)

---

Rework the Calls page (`/job/:jobId/calls`) into a **live batched call queue** and
wire the Call list page into it. Keep everything not mentioned as it is.

**0. Backend-authoritative global mode banner**

- Once the user is authenticated, the app shell must call `GET /api/runtime-config`
  with the Bearer token. Cache the result and refetch it on login/app reload:
  `{debug_mode, debug_behavior, debug_notice, demo_phone_configured,
  demo_phone_masked, twilio_number_configured, live_vendor_calls_enabled}`.
- When `debug_mode=true`, render a persistent, prominent banner above every page:
  **"DEBUG · transcript only"**, followed by `debug_notice`. Make it unmistakable
  that company identities come from Google Places but no vendor is contacted, no
  ElevenLabs conversation is opened, and no audio is generated. There is no client
  toggle: the backend value is authoritative.
- When debug is off, show **"Live outbound voice enabled"** only when
  `live_vendor_calls_enabled=true`; otherwise show **"Live vendors locked"**.
  The explicit allow-listed phone demo described below is the only real-call action
  available while debug mode is on.

**1. Prepare every Google vendor (Call list `/job/:jobId/call-list`)**

- Under the saved discovered-places table, count rows that have a phone number and
  whose `sources` includes `google_places`. Add one primary button:
  **"Use all {N} Google vendors"** →
  `POST /api/jobs/:jobId/companies/from-call-list {count: 0}`.
  `count: 0` means every callable Google Places vendor; never truncate or select a
  top-N subset. The operation is safe to repeat.
- Show the response `note`, then refetch the call queue and navigate to Calls.
  Do not offer fictional/simulated-company seeding on this path. Other discovery
  sources remain useful for deduplication and metadata, but only Google-backed rows
  become scheduled vendor identities.

**2. Live queue (`/job/:jobId/calls`)**

Poll `GET /api/jobs/:jobId/call-queue` every 3 seconds while this page is open and
immediately after every mutation. Its contract is:

```text
{
  debug_mode, debug_behavior, running,
  summary: {
    current_best_offer: null | {
      company_id, company_name, quote_id, total, binding, red_flags
    },
    offer_range: null | {low, high, count},
    called, total, calling, excluded_unverified_offers
  },
  batch: null | {
    run_id, index, count, size, status,
    knowledge_version, completed, total
  },
  follow_up_plan: [{
    company_id, company_name, reasons, source_quote_ids,
    knowledge_version, attempts, max_attempts, status
  }],
  queue: [{
    company: {id, name, phone, persona, source, discovery_sources},
    status, last_call_kind, conversation_id,
    initial_total, negotiated_total, red_flags,
    attempt_count, recalls_used, recalls_max, batch_index, knowledge_version,
    dial_mode, transcript_kind, follow_up
  }]
}
```

- Keep a **sticky panel at the very top** with three equal cards, driven only by
  `summary` (do not recompute the best offer in the browser):
  1. **Current best offer** — company + total, binding tag and red-flag count;
     `—` until one exists. The server already avoids high-risk offers when possible.
  2. **Offer range** — `$low–$high · count offers`; `—` until one exists.
  3. **Called** — `called / total`, plus `calling now` when non-zero.
- Directly below, show the batch barrier from `batch`: **"Batch index/count ·
  completed/total · knowledge vN"**, a progress bar, and its status. Add helper
  text: "Every call in this batch uses the same frozen facts. The next batch starts
  only after all calls here finish." Never fake client-side batch progress.
- One row per vendor. Show name, Google/source chips, attempt count, latest phase,
  batch and knowledge version, totals, red flags, and these status pills:
  `to_call` gray outline, `queued` gray, `calling` indigo/pulsing, `quote` green,
  `callback` amber, and `decline`/`hangup`/`failed` dark gray.
- Show a mode/evidence badge from `dial_mode` and `transcript_kind`:
  - `debug_transcript` or `debug_generated` → purple **"DEBUG-generated transcript"**;
  - `twilio_vendor` + `elevenlabs_voice` → blue **"Real vendor voice call"**;
  - `agent_bridge` + `elevenlabs_voice` → blue **"Agent voice simulation"**;
  - `demo_phone` → green **"Live demo · {demo_phone_masked}"**.
  Never label a debug transcript as a completed real call.

**Starting and recalling**

- **Start quote calls** (disabled when `running`, `summary.calling > 0`, the spec
  is unconfirmed, or there are no vendors) →
  `POST /api/jobs/:jobId/calls/start {phase: "quote", idempotency_key: <UUID>}`.
  Generate one UUID per deliberate click and reuse it only for transport retries.
  Do not send `parallel`.
  The server calls every eligible vendor in synchronous batches of
  `ceil(sqrt(n))`, and the response includes `{run_id, batch_size, batch_count,
  total, debug_mode}`. Show those values in a toast.
- Render `follow_up_plan` as a "Recommended recalls" panel. Each card shows
  `reasons`, `source_quote_ids`, and the knowledge version that justified it.
  A row/card **Recall to negotiate** button sends exactly
  `POST /api/jobs/:jobId/calls/start` with
  `{phase: "negotiate", company_ids: [company_id]}`.
  Support selecting several recommendations by sending their IDs in the same
  array. Include a fresh `idempotency_key`. Disable recalls while another run/call is
  active. A recall is a new attempt on the same vendor row, never a duplicate company.
  Show `recalls_used/recalls_max`; disable at 2/2 and render `status=exhausted`.
  The backend permanently rejects a third recall, including failed attempts.
- On 409 show the server detail (unconfirmed spec or active run); on 404 show that
  there are no eligible vendors/quotes; on 422 show validation errors.

**Transcripts, evidence, audio, and learned questions**

- Keep an attempt-history slide-over using `GET /api/jobs/:jobId/calls` and
  `/quotes`. A call record includes `transcript`, `transcript_kind`, `has_audio`,
  and authenticated relative `audio_url`.
- For `transcript_kind="debug_generated"`, show the purple badge and the explicit
  note **"Synthetic debug evidence — this vendor was not contacted; no recording
  exists."** For `elevenlabs_voice`, show **"Real voice transcript"**.
- When `has_audio=true`, fetch `API_BASE + audio_url` with the Bearer header as a
  Blob, play the resulting object URL in an `<audio controls>` element, and revoke
  it when the drawer closes. Do not put the protected URL directly in `<audio
  src>` because that request would omit the Authorization header. Hide the player
  when `audio_url` is empty.
- Detect each new terminal attempt (or `running: true → false`). Refetch the job,
  `GET /api/intake-form?vertical=<job.vertical>&area_code=<job.area_code>`, calls,
  quotes, report, and queue. This makes questions learned after every completed
  call appear in Intake/Spec without a page reload; toast when the learned-question
  count increases.

**3. Explicit allow-listed live phone demo**

At the bottom of Calls add a card:
**"📱 Live demo — call the configured human while preserving this vendor's identity."**

- There is **no phone-number input**. Show only `demo_phone_masked` from
  `/api/runtime-config`; the full allow-listed destination remains server-side.
- Select an existing Google vendor and phase (`quote`/`negotiate`) → **Call demo
  phone** → `POST /api/jobs/:jobId/calls/demo {company_id, phase}`.
- Disable the action unless `demo_phone_configured && twilio_number_configured`,
  the spec is confirmed, and no call/run is active. Before sending, confirm that
  this is a real Twilio/ElevenLabs call and the explicit exception to transcript-
  only debug mode.
- Show the masked `to_number` returned by the server, then poll queue/calls until
  the attempt resolves on the selected vendor row. On 503 show the configuration
  message, on 404 show "Vendor not found on this job", and on 502 show the
  ElevenLabs/Twilio error.
