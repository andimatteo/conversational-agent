# Lovable prompt — QuoteWise complete product + two-call live demo

Paste everything below the `---` into the **existing Lovable project as one
message**. Attach `assets/logo.png` to the same message if Lovable does not
already have it. The backend serves the generated repository PDF
`assets/demo/water_heater_intake.pdf` through an authenticated API, so do not
duplicate or rewrite it in Lovable. The prompt contains the current backend tunnel;
when that tunnel changes, update only the single `API_BASE` config value described
below.

---

Update the existing **QuoteWise** application into the complete end-to-end product
described here. Preserve working functionality that does not conflict with this
prompt. Implement the demo as a real backend-driven state machine; never fake a
call, transcript, quote, timer, batch transition or success state in the browser.

## Non-negotiable rules

1. The per-job journey is exactly: **Intake → Spec → Call list → Calls → Compare**.
2. One confirmed structured spec is reused across every quote call.
3. The prepared hybrid demo has one preselected Google Places identity represented
   by one consenting human at a server allow-listed destination. The Company name,
   saved Google phone and place ID remain unchanged; routing happens only on the
   backend.
4. The imported Twilio number is the **outbound source/caller ID**, not the demo
   destination. Never confuse the two and never send either number from the client.
   For this deployment the imported source ends in `5722`; the UI should show it
   only as the backend-provided masked caller ID (for example `•••5722`).
   Show only backend-provided masked values. There is no phone-number input anywhere
   in the demo UI.
5. The consenting human is in the **first quote batch**. The first phone call is
   exploratory quote gathering: ask sensible questions, obtain itemisation and
   terms, but do not haggle or mention competing offers.
6. Every other vendor in the demo is a truthful generated synthetic text
   conversation attached to a real Google Places identity. Stream its turns into
   the same persistent Call record that produces the Quote. Never label it as a
   voice call and never imply that the Google business was contacted.
7. Quote batches use the backend's `ceil(sqrt(n))` batch size and hard synchronous
   barriers. Every member of a batch uses one frozen knowledge snapshot; knowledge
   advances only after every call in that batch is terminal.
8. After **all** quote-batch barriers close, the backend automatically schedules one
   final negotiation callback to the same allow-listed human. The browser never
   POSTs a second call and never auto-retries a phone request.
9. The start confirmation explicitly authorizes exactly two real calls to the demo
   destination: initial exploration in quote batch 1, then the automatic grounded
   negotiation callback. The start payload must include
   `authorize_demo_calls: true`; without that explicit value the backend rejects a
   prepared hybrid run.
10. The automatic callback consumes the single demo follow-up. The prepared demo
    must finish with two live attempts total—one quote and one negotiation—and must
    not expose manual Recall or Negotiate buttons. More generally, show the
    backend's hard maximum of two recalls and never create an automatic call loop.
11. Synthetic leverage is usable only with an audible and visible disclosure. The
    agent may cite exact grounded database facts as a **simulated demo-market
    offer**; it must never claim that the named Google vendor made that offer.
12. The human's final offer becomes the highlighted best offer only if the verified,
    itemised, grounded negotiated total is truly the best according to the backend
    `summary.current_best_offer`. Do not predetermine, calculate or force a winner in
    the frontend.
13. Demo reset is operator-side, non-destructive and creates a fresh unconfirmed
    job. Do not invent a reset/delete API or UI button. Prior jobs, calls, quotes,
    uploads, transcripts and recordings remain auditable.
14. Do not create, edit or invoke any GitHub workflow, GitHub Action or `.github`
    file.

## Configuration, API client and auth

- Put this in exactly one frontend config module and import it everywhere:

  ```ts
  export const API_BASE =
    "https://travesti-championship-presented-machinery.trycloudflare.com";
  ```

  Do not duplicate the tunnel URL in components. If it changes, only this constant
  changes.
- All `/api/*` requests use `API_BASE`, JSON where applicable, and
  `Authorization: Bearer <token>`. Store the token in `localStorage`.
- Public endpoints are only `/api/auth/*`, `/api/verticals` and
  `/api/intake-form`. On any 401, clear auth and route to `/login`.
- Build one typed API helper that parses JSON error bodies and exposes the exact
  server `detail`. A failed request must produce an inline error or toast with a
  Retry action for read-only requests; never leave a blank page.
- A phone-call mutation is never automatically retried. For the one deliberate
  `/calls/start` click, create one UUID idempotency key and reuse it only if the same
  HTTP request has an ambiguous transport failure. A second deliberate run gets a
  new UUID.
- After login and on app reload call authenticated `GET /api/runtime-config` and
  cache:

  ```ts
  {
    debug_mode: boolean;
    debug_behavior: "transcript_only" | "hybrid_demo_roleplay" | "voice_and_telephony";
    debug_notice: string;
    demo_phone_configured: boolean;
    demo_phone_masked: string;
    twilio_number_configured: boolean;
    twilio_number_masked?: string;
    live_vendor_calls_enabled: boolean;
    demo_intake_pdf_url?: string; // currently "/api/demo/intake-pdf"
  }
  ```

  The backend is authoritative. There is no client debug toggle.
- In ordinary debug mode show a persistent **DEBUG · transcript only** banner plus
  the complete `debug_notice`. On a prepared job, replace the misleading global
  interpretation with a job-scoped **HYBRID DEMO · one consenting human, two live
  calls** banner. Explain that all other rows are generated demo-market text.

## Brand, layout and navigation

- Product name: **QuoteWise**. Tagline: *Your AI negotiator sees the fair price.*
- Use the attached `assets/logo.png` as-is: 32px in the sidebar, 48px in voice
  intake, 96px on login, and favicon. Do not redraw it.
- Light, trust-focused theme: white page background, `#EEF2FF` card surfaces,
  `#C7D2FE` borders, `#312E81` body, `#1E1B4B` headings, `#6366F1` primary and
  `#4338CA` hover. Reserve green/amber/red for statuses and risk.
- Desktop sidebar: Jobs, Domains, Profile. Mobile: compact drawer. On a job show the
  five tabs **Intake, Spec, Call list, Calls, Compare** and a small confirmed/
  awaiting-confirmation badge.
- Amounts are `$1,234`; dates use the user's locale. Use loading skeletons, clear
  empty states, keyboard focus, semantic labels and accessible color contrast.

## Demo state machine—the most important behavior

Render these backend states, never a scripted slideshow:

```text
PREPARED_UNCONFIRMED
  → DOCUMENT_UPLOADING → DOCUMENT_PARSED
  → VOICE_INTAKE_CONNECTED → READY_TO_REVIEW
  → CONFIRMED
  → AWAITING_EXPLICIT_TWO_CALL_CONSENT
  → QUOTE_BATCH_1 (live human explorer + synthetic peers)
  → QUOTE_BATCH_2 … QUOTE_BATCH_N (hard barriers, knowledge advances)
  → AUTO_NEGOTIATION_QUEUED
  → AUTO_NEGOTIATION_CALLING (same human, grounded closer)
  → COMPLETE → COMPARE
```

- The operator prepares a fresh job outside Lovable with
  `python -m negotiator.demo_reset` or selects the role-play identity with
  `python -m negotiator.demo_reset --live-vendor "Exact Google name"`. The command
  makes no call and returns a new `job_id`; the new job is unconfirmed. The UI only
  refreshes the jobs list and opens it.
- Never offer a destructive reset. Archived prior demos stay read-only and remain
  accessible for evidence.
- The selected target is server-provided by `job.demo_mode.live_company_id`. The UI
  may display it but may not edit it.
- `quote_batch_count` is the number of all-vendor quote batches.
  `auto_negotiation_batch` is the final automatic callback stage, normally
  `quote_batch_count + 1`. Use these backend values; do not infer them from elapsed
  time.
- The target's quote attempt must have `batch_index: 1`. Highlight it in batch 1 as
  **Live human · exploratory quote**. Its negotiation attempt must have
  `batch_index: auto_negotiation_batch`, `auto_negotiation: true`, and appear as
  **Live human · grounded negotiation**.
- When the last quote barrier closes, show **All market evidence collected · live
  callback queued** immediately. Show elapsed time since the barrier and copy
  **Expected within one minute** while it is queued. This is observational UI, not a
  client countdown that triggers a call. If 60 seconds pass, switch to an amber
  delayed state and keep polling; do not POST or redial.

## Authentication pages

### `/login`

Centered QuoteWise card with Sign in / Create account tabs.

- `POST /api/auth/login {email,password}`
- `POST /api/auth/register {email,password,name}`
- Both return `{token,user}`.
- Show demo hint `demo@negotiator.app / demo1234`.
- 401: wrong credentials; 409: already registered; 422: exact server detail.

Avatar menu: Profile and Logout. Logout calls `POST /api/auth/logout`, clears local
auth and routes to login.

### `/profile`

`GET /api/me` → `{user,jobs}`. Show profile metadata and the user's own jobs using
the same Job row as `/`.

## 1. Jobs — `/`

`GET /api/jobs`, newest first.

- Row: job ID, vertical, area, created date, source chips, document count,
  confirmed status, archived status, and demo badge when `demo_mode.roleplay=true`.
- For an active fresh demo show **Ready for document intake** when unconfirmed and
  open it on Intake.
- Archived jobs are read-only. Hide all mutations and show **Archived evidence**.
- Normal **New job** opens a domain/area modal using `GET /api/verticals`, then
  `POST /api/jobs {vertical,area_code}` and navigates to Intake. This does not turn
  an arbitrary job into a phone demo; only server-prepared `demo_mode` does that.
- Add a subtle **Refresh jobs** control so the operator can refresh after running
  the CLI reset. Do not shell out from Lovable.
- Empty: “No jobs yet — create one and let QuoteWise do the calling.”

## 2. Intake — `/job/:jobId/intake`

Load the Job and
`GET /api/intake-form?vertical=<job.vertical>&area_code=<job.area_code>`.
Everything is domain-configured; never hardcode plumbing fields in reusable form
components.

### A. Demo PDF first

At the top of an active, unconfirmed demo show **1 · Upload the system scope**.

- Above the dropzone, when `runtimeConfig.demo_intake_pdf_url` is non-empty, show
  **Download the demo intake PDF**. Fetch
  `API_BASE + runtimeConfig.demo_intake_pdf_url` with the Bearer header, convert the
  response to a Blob, create a temporary object URL, trigger a download named
  `QuoteWise-water-heater-intake.pdf`, then revoke the object URL. Do not navigate
  directly to the protected URL and do not put it in a normal unauthenticated
  anchor. The API currently maps that URL to authenticated
  `GET /api/demo/intake-pdf` and serves the unchanged repository asset
  `assets/demo/water_heater_intake.pdf`.
- Instruct the presenter to drag the downloaded PDF into the dropzone. This makes
  the visible demo exercise the same real upload/parser path as a user document.
- The generic dropzone accepts `.pdf .jpg .jpeg .png .webp .txt .md` and sends one
  multipart `file` per request to `POST /api/jobs/:jobId/documents`.
- While parsing: “Reading equipment and installation details…” with progress state.
  Never manufacture extraction results locally.
- Render the response's `document`: filename, upload time, `extracted_fields`,
  `updates`, `insights`, validation warnings and quote-on-file badge. Refetch Job,
  Documents and Intake Form after success.
- The PDF should fill most of the scope. Visually flash fields changed by parsing.
  Keep its remaining unknowns visibly marked so the short voice intake can ask only
  for those.
- 415 lists accepted formats. 422 shows schema validation. 503 says document
  parsing is unavailable. Preserve the selected file so the user can retry a failed
  upload deliberately.

### B. Short missing-details browser call

Directly below show **2 · Confirm the last details by voice**. Install and use
`@elevenlabs/react`.

- Before starting, compare `job.spec` with `spec_schema.required` and show:
  **“The document supplied X fields; Y required details remain.”** This is only UI
  guidance; the Estimator and backend tools remain authoritative.
- For the supplied water-heater PDF, the intended short exchange confirms three
  things: urgency (`this_week` or flexible), whether the main shutoff is known and
  operational, and a normal-hours weekday access window. Show those as “expected
  confirmations” only when they are still absent; never ask them again after they
  have been saved, and never use a hardcoded three-question count as a confirmation
  gate.
- `POST /api/jobs/:jobId/voice-session` →
  `{signed_url,agent_id,dynamic_variables}`. Pass the response values unchanged to
  `useConversation().startSession({signedUrl,dynamicVariables})`.
- The Estimator must receive the current saved spec and ask only for missing,
  ambiguous or explicitly unconfirmed facts. It should briefly validate the job,
  not repeat details already grounded in the PDF. UI copy: **“A quick call—most of
  the job is already in the document.”**
- Request microphone only after the deliberate Start click. While connected show
  timer, speaking/listening orb, connection status, live chat bubbles from SDK
  `onMessage`, and **End call**. Do not create fake transcript messages.
- On disconnect, toast “Interview saved”, then refetch Job and Intake Form. Flash
  changed fields. Any changed spec remains unconfirmed.
- Mic denial, disconnected session, 502 and 503 all show exact actionable copy.
  Never treat a failed browser call as a completed intake.

### C. Schema-driven form

- Render `spec_schema.fields`: enum→select, bool→toggle, number/int→number,
  str→text, date→date, object→fieldset, list→repeatable rows. Star required fields.
- Prefill only from `job.spec`; document, voice and form are three views of the same
  server object.
- `PUT /api/jobs/:jobId/spec {spec}`. Highlight returned
  `missing_required_fields`. Any edit resets confirmation.
- Render base questions as helper copy and `learned_questions` with question,
  why-it-matters and times seen. Learned answers append safely to notes.
- When no required field is missing, CTA **Review complete job spec** routes to Spec.

## 3. Spec — `/job/:jobId`

Render the structured spec generically: cards for objects, tables for lists and
label/value rows for scalars. Show document and voice provenance, missing values,
existing quote evidence, and learned questions.

- Active demo header: **3 · Review and authorize the quote run**.
- `POST /api/jobs/:jobId/confirm` confirms only the spec. On 422 list every exact
  validation error. Do not start calls from this endpoint.
- Confirmation is reset by later spec changes. Calls remain locked until confirmed.
- After confirmation, show **Continue to Call list**. The Calls start action still
  requires separate explicit two-call consent.
- Archived jobs are read-only.

## 4. Call list — `/job/:jobId/call-list`

`GET /api/jobs/:jobId/call-list`.

- Show Google Places, Yelp and OSM provider status; table columns for name, E.164
  phone, address/city, rating/reviews, categories, source IDs and URL.
- Mark a row **Google · callable** only when it has a phone and
  `sources.includes("google_places")`.
- Normal market scan uses `POST /api/jobs/:jobId/call-list/discover
  {state,query,target_per_provider}`. A partial scan is diagnostic, not saved and
  cannot replace a complete list.
- A normal saved scan uses **Use all N Google vendors** →
  `POST /api/jobs/:jobId/companies/from-call-list {count:0}`. Never truncate to a
  top-N selection.
- A prepared demo already has all Google-callable companies promoted. Show every
  one, disable arbitrary selection and mark exactly one server-selected row:
  **Consenting human role-play · batch 1**. Explain that the saved business number
  will not be dialled; the backend routes that identity to the allow-listed demo
  destination.
- CTA **Continue to Calls**. Do not expose company phone editing.

## 5. Calls — `/job/:jobId/calls`

This is the demo's main stage. Poll `GET /api/jobs/:jobId/call-queue` every second
while any call/transcript is active, every three seconds while queued or between
batches, and once after every terminal transition. Also poll Calls and Quotes as
described under Realtime below. Pause background polling when the tab is hidden,
then refetch immediately on visibility.

### Sticky real-time outcome panel

Pin three equal cards below the banners, driven **only** by `queue.summary`:

1. **Current best offer** — company, total, binding badge, risk count; `—` if none.
2. **Observed offer range** — `$low–$high · count offers`; `—` if none.
3. **Called** — `called / total`, plus `calling now` and a progress bar.

Never calculate a winner or range in JavaScript. Animate a value change subtly and
show “Updated from call evidence”, but retain the exact server number.

### Explicit consent and start

When confirmed and not started, show a prominent demo card with:

- selected Google identity and **batch 1 explorer** badge;
- masked role-player destination from runtime/demo state;
- imported Twilio source as configured/masked, clearly labelled **caller ID**;
- `N` total Google vendors and server-previewed batch layout;
- truth statement: N−1 businesses are not called; their conversations are generated
  text and labelled synthetic;
- two-stage diagram: **Call 1 · explore and quote** → all quote barriers →
  **Call 2 · automatically negotiate**.

The Start button is disabled unless the spec is confirmed, vendors exist, no run is
active, `demo_phone_configured=true` and `twilio_number_configured=true`.

Before the request, require one unchecked checkbox with this exact meaning:

> I authorize QuoteWise to make two real calls to the configured demo role-player:
> an exploratory quote call in the first batch and one automatic negotiation
> callback after all quote batches finish. No Google business will be called.

The button label is **Authorize 2 calls and start all-vendor demo**. On click send
exactly:

```http
POST /api/jobs/{job_id}/calls/start
Content-Type: application/json

{
  "phase": "quote",
  "idempotency_key": "<one fresh UUID for this deliberate click>",
  "authorize_demo_calls": true
}
```

Do not send `company_ids`, `parallel`, a phone, a target or a separate negotiation
request. Disable the control immediately. The response supplies authoritative
`run_id`, `total`, `batch_size`, `quote_batch_count`,
`batch_count`, `auto_negotiation_batch`, `auto_negotiation_status`, `total_calls`,
`demo_calls_authorized`, `live_company_id` and safe destination marker. Here `total`
is the N-vendor quote market, `total_calls` is N+1 because it includes the automatic
callback, and `batch_count` includes the callback batch.

For ordinary non-demo jobs, retain the normal **Start quote calls** request without
`authorize_demo_calls`; ordinary negotiation remains backend-policy driven. Never
show the prepared demo's special confirmation on an ordinary job.

For ordinary jobs only, render `follow_up_plan` as **Recommended recalls** with
reason, source quote IDs and knowledge version. A deliberate recall sends
`POST /api/jobs/:jobId/calls/start` with
`{phase:"negotiate", company_ids:[...], idempotency_key:"<fresh UUID>"}`. Disable
it while any run is active and at `recalls_used === recalls_max`. The backend hard
limit is two recalls per vendor, including failed attempts; never hide, reset or
automatically bypass that count. The prepared hybrid demo hides these controls
because its one allowed callback is already scheduled by the server.

### Batch timeline and barriers

Show a horizontal/vertical timeline with:

- Quote batch 1 through `quote_batch_count`;
- final **Automatic negotiation callback** at
  `auto_negotiation_batch`;
- current `batch.index`, `batch.phase`, status, `completed/total`, batch size and
  `knowledge_version`;
- frozen-snapshot caption: “Every conversation in this batch sees the same facts.
  Knowledge updates only after all calls here finish.”

The target human is visibly in quote batch 1. Do not suggest that it can use offers
from batch 1 peers or later batches during the first call. After each hard barrier,
animate the knowledge badge from vN to vN+1 and update offers only from API data.

When the final quote batch completes, append the final callback stage automatically
and show **Grounded negotiation context ready** with exact eligible quote/evidence
counts. There is no button for this callback.

Map the backend callback state without renaming it: `waiting_for_quote_batches` →
waiting behind barriers; `running` plus a callback Call in `queued` → queued;
`running` plus a callback Call in `calling` → ringing/in progress; `completed` →
terminal success; `failed` → terminal failure; `not_requested` → no automatic
callback for this run.

### Vendor rows

One stable row per Company; attempts nest under the row rather than duplicating the
vendor. Show name, Google/source badges, rating, status, totals, flags, attempts,
batch and knowledge version.

Status pills: `to_call`, `queued`, `calling`, `quote`, `callback`, `decline`,
`hangup`, `failed`, and `completed`. Mode/evidence badges:

- `debug_transcript` / `debug_generated_streaming` / `debug_generated`: purple
  **Synthetic demo-market chat · Google business not contacted**;
- `demo_phone` + quote: green **Live human · exploratory quote**;
- `demo_phone` + negotiate: green **Live human · negotiation callback**;
- `twilio_vendor` + `elevenlabs_voice`: blue **Real vendor voice call** only on
  genuine non-demo vendor calls;
- `agent_bridge`: blue **Agent voice simulation**.

The selected target row shows two attempt cards as they happen: initial quote and
negotiation. Never overwrite the first transcript with the second.

### Realtime transcripts from the same Call records

Poll `GET /api/jobs/:jobId/calls` every 750–1000ms while any returned call has
`transcript_streaming=true`, otherwise with the queue cadence. Use `call.id` as the
stable key and append/update only when `transcript_turn_count`,
`last_transcript_at` or transcript length increases. Do not generate either side of
the chat in Lovable.

- A streaming synthetic Call remains one record from first turn through terminal
  outcome. Its final Quote from `GET /quotes` must have the same `call_id`. Display
  a provenance link **Offer extracted from this conversation**. The backend publishes
  that Quote only after the Call reaches `status="completed"`; while turns are still
  streaming, show “Quote pending conversation completion” and never synthesize a
  provisional total in the browser.
- An open attempt drawer shows agent/vendor bubbles, live cursor,
  `transcript_turn_count`, phase, knowledge version and frozen snapshot badge.
  Preserve scroll position unless the viewer is already at the bottom.
- If a Quote references an unknown Call, show **Evidence unavailable** and exclude
  it from client presentation; never attach it heuristically to a nearby transcript.
- For synthetic records show: “AI-generated demo conversation. This Google business
  was not contacted; no recording exists.”
- For live role-play show: “Consenting human role-play. This person does not
  represent the displayed Google business.”
- If ElevenLabs exposes live partial turns, append them using the same Call fields.
  If it exposes the transcript only after provider completion, show **Live call in
  progress · transcript will appear when finalized**. Never invent live phone turns
  merely to match the synthetic streaming animation.
- On the negotiation transcript visually highlight exact grounded leverage mentions
  and their `leverage_quote_ids`. Show the disclosure in the same turn. If grounding
  verification fails, show a red evidence warning and do not celebrate a concession.

### Automatic callback and expected final best

After all quote batches are terminal, the backend schedules the same human. Show:

- current stage `AUTO_NEGOTIATION_QUEUED` then `CALLING`;
- final frozen knowledge version;
- the target's own verified initial total;
- eligible simulated demo-market offers with quote IDs, totals and evidence kind;
- copy that these may be used only with explicit simulation disclosure.

When the call ends, compare nothing locally. If
`summary.current_best_offer.company_id === demo_mode.live_company_id` and the latest
negotiated Quote is `evidence_verified`, `grounding_verified` and
`itemization_verified`, show a green hero:

**New best offer, verified in the live negotiation**

Show initial → negotiated total, savings, binding/terms and transcript evidence. If
the human does not become best, show the honest result and the actual server-selected
best. The demo is not marked failed merely because a planned concession did not
happen.

### Learned questions

On every newly terminal call, refetch Job and
`GET /api/intake-form?vertical=...&area_code=...`. Toast when learned-question count
increases and show a compact **What this market taught us** panel with question,
why-it-matters, vendor/call provenance and frequency. Do not wait for the entire run.

### Audio with authenticated fetch

For `has_audio=true`, never put a protected relative URL directly in `<audio src>`.
Fetch `API_BASE + audio_url` with the Bearer header, convert to Blob, create an object
URL and pass that to `<audio controls>`. Revoke it on drawer close/unmount. Hide audio
for synthetic calls and do not render a broken player.

## 6. Compare — `/job/:jobId/compare`

`GET /api/jobs/:jobId/report` after each terminal transition and when the run ends.

- Benchmark band: fair low, median, fair high, red-flag floor and every final total.
- Server recommendation hero; do not recompute ranking.
- Ranked cards: company, score, initial/negotiated/final totals, savings, binding,
  itemised fees, conditions, red flags and evidence accordion.
- Evidence lists call ID, quote ID, phase, verbatim passage, conversation ID,
  verification fields and audio where available.
- Give purple synthetic evidence and green role-play evidence distinct permanent
  labels. The report must not say “book this real vendor” for a human role-play
  identity.
- When the target is genuinely best, connect its ranked card to the exact negotiated
  transcript passage and leverage IDs. If not, explain the actual recommendation.
- Empty: “No grounded quotes gathered yet.”

## 7. Domains — `/domains`

`GET /api/verticals`. Render display name, vertical, area, file and validity. Keep
the domain-agnostic generator:
`POST /api/verticals/generate {vertical,area_code,notes,force?}`. Handle 409 with an
explicit overwrite confirmation and never overwrite silently.

## Authoritative API contracts

Use tolerant TypeScript types for additive fields, but do not rename these fields or
substitute client-derived state.

```ts
type RedFlag = {id: string; severity: "low" | "medium" | "high"; label: string};
type LearnedQuestion = {
  question: string; why_it_matters: string; times_seen?: number;
  source_call_id?: string; company_id?: string;
};
type FollowUp = {
  company_id: string; company_name: string; reasons: string[];
  source_quote_ids: string[]; knowledge_version: number;
  attempts: number; max_attempts: number; status: string;
};
type DocumentRecord = {
  id: string; filename: string; uploaded_at: string;
  extracted_fields: string[];
  updates: Array<{field: string; from: unknown; to: unknown}>;
  has_quote: boolean; insights: string[]; validation_errors?: unknown[];
};
```

### Job

```ts
type Job = {
  id: string;
  vertical: string;
  area_code: string;
  spec: Record<string, unknown>;
  spec_source: string;
  confirmed: boolean;
  archived?: boolean;
  created_at: string;
  documents?: DocumentRecord[];
  discovered_questions?: LearnedQuestion[];
  knowledge_version?: number;
  follow_up_plan?: FollowUp[];
  demo_mode?: null | {
    active: boolean;
    roleplay: true;
    status: string;
    session_id: string;
    live_company_id: string;
    live_company_name: string;
    notice?: string;
    quote_batch_count?: number;
    auto_negotiation_batch?: number;
    auto_negotiation_status?: string;
    auto_negotiate?: boolean;
    demo_calls_authorized?: boolean;
  };
};
```

### Queue

```ts
type CallQueue = {
  debug_mode: boolean;
  debug_behavior: string;
  running: boolean;
  demo_mode: null | {
    roleplay: true;
    session_id: string;
    live_company_id: string;
    live_company_name: string;
    destination: "configured_demo_phone";
    notice: string;
    auto_negotiate?: boolean;
    quote_batch_count?: number;
    auto_negotiation_batch?: number;
    auto_negotiation_status?: string;
    demo_calls_authorized?: boolean;
  };
  summary: {
    current_best_offer: null | {
      company_id: string;
      company_name: string;
      quote_id: string;
      total: number;
      binding: boolean;
      red_flags: RedFlag[];
    };
    offer_range: null | {low: number; high: number; count: number};
    called: number;
    total: number;
    calling: number;
    excluded_unverified_offers: number;
  };
  batch: null | {
    run_id: string;
    index: number;
    count: number;
    size: number;
    quote_batch_count: number;
    auto_negotiation_batch: number | null;
    auto_negotiation_status:
      | "not_requested" | "waiting_for_quote_batches"
      | "running" | "completed" | "failed";
    phase: "quote" | "negotiate";
    status: string;
    knowledge_version: number;
    completed: number;
    total: number;
  };
  follow_up_plan: FollowUp[];
  queue: Array<{
    company: {
      id: string;
      name: string;
      phone?: string;
      source: string;
      discovery_sources: string[];
      rating?: number;
    };
    status: string;
    last_call_kind: "" | "quote" | "negotiate";
    conversation_id?: string;
    initial_total: number | null;
    negotiated_total: number | null;
    red_flags: RedFlag[];
    attempt_count: number;
    recalls_used: number;
    recalls_max: number;
    batch_index: number | null;
    knowledge_version: number | null;
    dial_mode: string;
    transcript_kind: string;
    transcript_streaming: boolean;
    transcript_turn_count: number;
    last_transcript_at: string;
    last_transcript_turn: null | {role: string; text: string};
    follow_up?: FollowUp;
  }>;
};
```

### Call and Quote

```ts
type CallRecord = {
  id: string;
  job_id: string;
  company_id: string;
  kind: "quote" | "negotiate";
  status: string;
  outcome?: "quote" | "callback" | "decline" | "hangup" | "failed";
  mode: "debug_transcript" | "demo_phone" | "twilio_vendor" | "agent_bridge";
  transcript: Array<{role: "agent" | "vendor" | "user"; text: string}>;
  transcript_kind?: "" | "debug_generated_streaming" | "debug_generated"
    | "elevenlabs_voice" | "none";
  transcript_streaming?: boolean;
  transcript_turn_count?: number;
  last_transcript_at?: string;
  conversation_id?: string;
  attempt_number?: number;
  batch_index?: number;
  knowledge_version?: number;
  started_at?: string;
  ended_at?: string;
  has_audio: boolean;
  audio_url: string;
  demo_roleplay?: boolean;
  auto_negotiation?: boolean;
  recall_slot?: number;
};

type Quote = {
  id: string;
  company_id: string;
  call_id: string;
  phase: "initial" | "negotiated";
  total: number;
  binding: boolean;
  deposit: number;
  valid_until?: string;
  line_items: Array<{
    label: string; code: string; amount: number; kind: string;
  }>;
  conditions: string[];
  red_flags: RedFlag[];
  verbatim_evidence: string;
  evidence_kind?: string;
  evidence_verified?: boolean;
  grounding_verified?: boolean;
  itemization_verified?: boolean;
  leverage_quote_ids?: string[];
  knowledge_version?: number;
};
```

### Start response

```ts
type StartDemoResponse = {
  started: boolean;
  run_id: string;
  phase: "quote";
  total: number;
  total_calls: number;
  batch_size: number;
  batch_count: number;
  quote_batch_count: number;
  auto_negotiation_batch: number;
  auto_negotiation_status: string;
  debug_mode: boolean;
  demo_roleplay: true;
  demo_calls_authorized: true;
  live_company_id: string;
  live_destination: "configured_demo_phone";
};
```

If a newly additive field is absent during rollout, show a neutral “Waiting for
orchestration metadata” state and keep polling. Do not guess a batch index or enable
a call action as fallback.

## Realtime data refresh matrix

| Event | Immediately refetch |
|---|---|
| Document parsed | Job, Documents, Intake Form |
| Voice session disconnected | Job, Intake Form |
| Spec saved/confirmed | Job, Queue |
| Run start accepted | Queue, Calls, Quotes |
| `transcript_turn_count` changes | Calls; Quotes only after the Call becomes terminal |
| Batch barrier closes | Queue, Calls, Quotes, Job, Intake Form, Report |
| Auto callback changes state | Queue, Calls, Quotes |
| Entire run terminal | Queue, Calls, Quotes, Job, Intake Form, Report |

Deduplicate by IDs. Abort stale fetches on job/route change. Do not let an older poll
overwrite a newer response.

## Error and safety behavior

- 401: clear auth and route to login.
- 404: show exact missing Job, vendor, quote or evidence message.
- 409: show unconfirmed spec, active run, missing explicit demo authorization or
  archived/read-only conflict. Never bypass it.
- 415: accepted document formats.
- 422: render validation details beside relevant fields.
- 502: provider/tunnel failure; preserve known state and do not redial.
- 503: missing ElevenLabs/Twilio/demo configuration or unmet barrier. Display the
  exact detail and a Preflight checklist; never downgrade to a fake success.
- Unknown external provider state: red locked banner **Automatic redial suppressed**.
- A failed synthetic call remains failed; do not invent a quote.
- A failed initial human call blocks automatic negotiation and is reported honestly.
- A failed automatic callback remains one consumed attempt. No browser retry loop.
- If audio is missing, keep transcript/evidence but never claim a recording exists.

## Final acceptance checklist

- A new prepared demo opens unconfirmed and can ingest the supplied PDF through the
  real multipart parser.
- Browser voice intake is brief and asks only what the document did not settle.
- User reviews and confirms the one shared structured spec.
- One explicit checkbox authorizes two live phone calls; request sends
  `authorize_demo_calls:true` and no phone/company subset.
- The allow-listed human is visibly in **quote batch 1** as explorer, with no
  negotiation leverage.
- Synthetic conversations stream progressively from backend Call records; every
  structured Quote references the same `call_id`.
- Sticky best/range/called cards change from server evidence in real time.
- Hard barriers and knowledge versions are visible and truthful.
- After all quote batches, the negotiation callback starts automatically, normally
  within one minute, without a frontend POST or retry.
- The Closer cites only exact grounded offers and calls every synthetic one a
  simulated demo-market offer.
- The final live offer is celebrated as best only when the backend verifies and
  ranks it as best.
- Both live attempts remain under one vendor's history; synthetic vendors retain
  permanent disclosure labels; protected audio uses authenticated Blob fetching.
- No raw phone input, no destructive reset, no fake timers/data, no GitHub workflow.
