# Lovable prompt — QuoteWise dashboard

> **Superseded:** this file contains the retired Call List page. Use
> `docs/LOVABLE_DEMO_COMPLETE_PROMPT.md` for the live Google Places review/launch flow.

How to use: paste everything below the `---` into a NEW Lovable project
(lovable.dev) as the first message, **attaching `assets/logo.png` to that same
message** (it's the official logo). The backend (FastAPI + cloudflared tunnel)
must be running; CORS is open. If the tunnel restarts, edit the `API_BASE`
constant inside Lovable — the prompt tells it to keep that in one config file.

---

Build **QuoteWise** — a clean, trust-focused web dashboard for a service whose AI
voice agents interview a customer about a job, phone local companies for itemised
quotes, and negotiate the best deal on the customer's behalf.

QuoteWise is **domain-agnostic**: today it does plumbing, tomorrow HVAC or moving,
purely by configuration. Therefore NEVER hardcode trade-specific fields or labels —
every form, spec view and label must render from the schemas the API returns.

## Brand & design

- Name: **QuoteWise**. Wordmark: "Quote" in regular weight + "Wise" in semibold,
  accent color indigo `#6366F1`. Tagline (login page, under the logo):
  *"Your AI negotiator sees the fair price."*
- Logo: **the image attached to this message** — a crystal ball with a phone
  inside, on a purple stand. Use it as-is (never redraw it): sidebar header at
  32px beside the wordmark, login page at 96px, and as the favicon. It was born
  on white, so it sits directly on the light surfaces with no backing needed.
  If no image is attached, ask for it before styling the header.
- **Light professional theme in the logo's colors**: page background white
  `#FFFFFF`, section/card surfaces near-white `#EEF2FF`, hairline lavender
  `#C7D2FE` borders, body text deep indigo `#312E81`, headings `#1E1B4B`,
  primary buttons and links indigo `#6366F1` (hover `#4338CA`), active/selected
  states on lavender `#A5B4FC` tints, soft shadows, rounded corners, generous
  whitespace. Keep green/amber/red ONLY for status badges and red flags so they
  pop against the indigo palette.
- Evidence-first: verbatim quotes, red flags and dollar amounts are the heroes.
  Format every amount as `$1,234`.

## Ground rules

- All data comes from a REST API. Define `const API_BASE` in ONE config file,
  default `https://partner-may-cheers-switched.trycloudflare.com`.
- Auth is a bearer token: store it in localStorage and attach
  `Authorization: Bearer <token>` to EVERY `/api/*` request. Public without token:
  `/api/auth/*`, `/api/verticals`, `/api/intake-form`.
- On any 401: clear the token, redirect to `/login`.
- On fetch errors: small toast + retry button, never a blank screen.
- Poll every 5s only where marked. Empty states everywhere, written in plain
  language (each page's is specified below).

## Auth

**`/login`** — centered card: logo 96px, wordmark, tagline. Tabs **Sign in** /
**Create account**.
- Sign in → `POST /api/auth/login {email, password}`; register →
  `POST /api/auth/register {email, password, name}`. Both return `{token, user}`.
- Errors: 401 "Wrong email or password", 409 "Email already registered",
  422 shows the server message.
- Hint under the card: demo account `demo@negotiator.app / demo1234`.
- Every user sees ONLY their own jobs — the API enforces it (other users' jobs
  are 404). Top-right avatar menu: name/email, **Profile**, **Logout**
  (`POST /api/auth/logout`, then clear token).

**`/profile`** — `GET /api/me` → `{user, jobs}`: profile card (name, email,
member since) + the user's jobs with the same row component as the Jobs page.

## Pages

### 1. Jobs — `/`
`GET /api/jobs` (newest first). Row: job id (mono), vertical + area_code chips,
status badge (green "confirmed" / amber "awaiting confirmation"), spec_source
chips (`interview` / `document` / `form` / `sample`), created date, count badge
"N docs" when `documents` is non-empty. Click → job page.
**New job** button → modal: `GET /api/verticals` → pick domain/area
(display_name, vertical, area_code) → `POST /api/jobs {vertical, area_code}` →
navigate to its Intake tab.
Empty state: "No jobs yet — create one and let QuoteWise do the calling."

### 2. Intake — `/job/:jobId/intake`
`GET /api/intake-form?vertical=<job.vertical>&area_code=<job.area_code>` →
`{spec_schema, base_questions, learned_questions}`.

- **🎙 Voice intake card — FIRST thing on the page.** Install the
  `@elevenlabs/react` npm package. Card: the logo at 48px, "Talk to the
  estimator", subtitle "A 3-minute call fills this whole form", big indigo
  **Start voice intake** button. On click: `POST /api/jobs/:jobId/voice-session`
  → `{signed_url, dynamic_variables}`, then start the conversation with
  `useConversation()` and `conversation.startSession({ signedUrl,
  dynamicVariables })` (browser asks for mic permission).
  - While connected: replace the button with a pulsing indigo orb (speaking =
    strong pulse, listening = soft), a live transcript as chat bubbles (agent
    left, you right, from the SDK's `onMessage`), and a red **End call** button
    → `conversation.endSession()`.
  - On disconnect: toast "Interview saved", refetch the job AND this form —
    the voice interview fills the same spec the form edits.
  - Errors: mic permission denied → "Allow microphone access to talk to the
    estimator"; 502/503 from voice-session → show the server's message.
- Build the form FROM `spec_schema.fields` (domain-agnostic!): `enum`→select,
  `bool`→toggle, `int`/`number`→number, `str`→text, `date`→date picker,
  `object`→grouped fieldset of its `fields`, `list`→repeatable rows of its
  `item_fields`. Star the `spec_schema.required` names. Show `base_questions[i]`
  as helper text near the best-matching field; leftovers go in an info panel
  "The estimator would also ask".
- **The form is a live view of `job.spec`**: prefill every field from it (the
  voice call, document uploads and this form all edit the SAME spec). After a
  voice call ends or a document is parsed, refetch the job and update the form
  values in place, briefly highlighting (lavender flash) each field that was
  filled or changed.
- **Learned from calls** section: one highlighted card per entry of
  `learned_questions` (`{question, why_it_matters, times_seen}`): "📚 learned
  from calls" badge, the question as a free-text input, `why_it_matters` as
  tooltip, "seen ×N" tag. Append answers to the spec's `notes` as "Q: … A: …".
  Empty state: "No learned questions in this area yet — calls will teach the form."
- Save → `PUT /api/jobs/:jobId/spec {spec}` → returns `missing_required_fields`:
  non-empty ⇒ highlight those fields + amber banner; empty ⇒ show **Confirm
  spec** CTA.
- Compact document dropzone card too (same behavior as the Documents panel below).

### 3. Job & Spec — `/job/:jobId`
`GET /api/jobs/:jobId`.
- Render the spec generically: one card per top-level key (object → key/value
  grid, array → table, scalar → labelled value). No trade-specific layout.
- Big **Confirm spec** button → `POST /api/jobs/:jobId/confirm`. Until confirmed:
  banner "🔒 Calls locked until you confirm". On 422 list the missing fields the
  server returns. Any spec change resets confirmation server-side — reflect it.
- "🧠 What this job taught the intake form" panel when `discovered_questions` is
  non-empty (list of `{question, why_it_matters}`).
- **Documents panel** — the second intake door beside the call:
  - Dropzone "Add documents — other quotes, equipment/system specs, photos"
    accepting `.pdf .jpg .jpeg .png .webp .txt .md` →
    `POST /api/jobs/:jobId/documents` (multipart `file`, one per request),
    spinner "Reading your document…" (~10s).
  - List from `GET /api/jobs/:jobId/documents`: filename, date, chips of
    `extracted_fields`, amber diff chips for each entry of `updates`
    (`field: from → to`, e.g. "floor: 7 → 2"), gold "💰 quote on file" badge
    when `has_quote`, `insights[]` as small gray lines.
  - A parsed document doesn't just add notes — it UPDATES the intake: new
    fields get filled and existing fields get corrected (`updates`). After
    upload: toast summarising both (e.g. "Filled property_age_years · updated
    floor 7 → 2 · +1 competing quote"), refetch the job, update the spec view
    AND the intake form values in place, surface the re-confirm banner.
  - If the spec has `existing_quotes`: "Leverage on file" card — company + total
    each, caption "our negotiator will cite these".
  - Errors: 503 "Document parsing needs the OpenAI key on the server",
    415 lists accepted formats.

### 4. Call list — `/job/:jobId/call-list`
This is the real-world market source for the Caller. On load call
`GET /api/jobs/:jobId/call-list`. Show state, optional query, target per provider,
and fixed Google Places, Yelp and OpenStreetMap source badges. "Scan the market"
calls `POST /api/jobs/:jobId/call-list/discover` with
`{state, query, target_per_provider}` without clearing the previous saved list.

Render `provider_status[source]` from `{status, results?, reason?}`, `total`,
`raw_results`, and a searchable table from `items`: name, E.164 phone, sources,
rating/reviews, address/city, categories and URL. Mark rows with a phone and
`sources.includes("google_places")` as **Google · callable**. A partial scan has
`complete=false, saved=false`: keep it visible for diagnosis but do not let it
replace/prepare the market. Empty state: "Scan the market to find who we should call."

For a non-empty saved list, count every Google-callable row and show one action:
**Use all N Google vendors** →
`POST /api/jobs/:jobId/companies/from-call-list {count: 0}`. Zero means all: never
truncate to a top-N subset and do not offer fictional companies. Show the response
`note`/`debug_mode`, refetch the call queue, and navigate to Calls.

### 5. Calls — `/job/:jobId/calls` (poll 3s)
Poll `GET /api/jobs/:jobId/call-queue`. Keep a sticky top panel driven by its
server-computed `summary`: **Current best offer** (company, total, binding/flags),
**Offer range** (`low–high`, count), and **Called** (`called / total`, plus calling).
Do not calculate a cheapest winner in the browser.

Below it show `batch` as "Batch index/count · completed/total · knowledge vN" with
a progress bar and the explanation that every call in the batch uses one frozen
knowledge snapshot; the server starts the next batch only after all calls finish.
Rows from `queue` show company/source, status, phase, totals, flags, attempt count,
batch/knowledge version and mode. Badge `debug_transcript`/`debug_generated` as
**DEBUG-generated transcript**; `twilio_vendor`/`elevenlabs_voice` as **Real vendor
voice call**; `agent_bridge` as **Agent voice simulation**; `demo_phone` as
**Live demo · masked number**. Never imply a debug vendor was contacted.

**Start quote calls** → `POST /api/jobs/:jobId/calls/start
{phase: "quote", idempotency_key: <UUID>}`. Reuse that UUID only for transport retries.
Never send `parallel`: the server schedules every eligible vendor in synchronous
`ceil(sqrt(n))` batches. Disable mutations while `running` or `summary.calling > 0`.
Render `follow_up_plan` with reasons, source quote IDs and knowledge version. Recall
one or several vendors through `POST /api/jobs/:jobId/calls/start` with
`{phase: "negotiate", company_ids: [id, ...], idempotency_key: <fresh UUID>}`;
each is another attempt on the same row. Show `recalls_used/recalls_max` and disable
recalls at 2/2; the backend rejects a third attempt even if an earlier one failed.

The transcript slide-over reads `GET /api/jobs/:jobId/calls` + `/quotes`. Show a
purple **Synthetic debug evidence — vendor not contacted, no recording** notice for
`transcript_kind="debug_generated"`, or **Real voice transcript** for
`elevenlabs_voice`. When `has_audio`, fetch `API_BASE + audio_url` with the Bearer
header as a Blob and give its object URL to `<audio controls>`; revoke it on close.

Add an explicit live-demo card with no raw phone input. Show only the allow-listed
`demo_phone_masked` from `/api/runtime-config`, select an existing Google vendor and
phase, then `POST /api/jobs/:jobId/calls/demo {company_id, phase}`. Require confirmation
that this is a real Twilio/ElevenLabs call and the explicit exception to debug mode;
enable only when both phone configuration flags are true and no call is active.

After every newly terminal attempt (and on `running: true → false`), refetch the job,
the area/domain intake form, queue, calls, quotes and report. Toast when the learned-
question count grows so Intake and Spec update without a page reload. Empty state:
"Prepare Google vendors from Call list to start gathering offers."

### 6. Comparison — `/job/:jobId/compare`
`GET /api/jobs/:jobId/report`.
- Benchmark strip: `fair_low / median / fair_high` as a horizontal band,
  each company's `final_total` plotted, `red_flag_floor` as a red line.
- Winner card on top with the plain-language `recommendation` prominent.
- Ranked cards from `ranking[]`: rank #, company, score, `initial_total` struck
  through when `negotiated_total` improved it, "saved $X in negotiation" badge,
  `line_items` table (label/code/amount/kind), red-flag chips (full `label` on
  hover), **Evidence** accordion of `evidence[]` (phase, verbatim quote in
  italics, conversation id in mono).
- Footer "Market evidence": `market_evidence[]`.
- Empty state: "No quotes gathered yet."

### 7. Domains — `/domains`
- `GET /api/verticals` → table: display_name, vertical, area_code, file, valid ✓/✗.
- "**Generate a domain sheet with AI**" card: vertical (slug), area_code, notes
  (textarea) → `POST /api/verticals/generate {vertical, area_code, notes}` →
  spinner "The AI is writing the config sheet… (~1 min)" → on success show
  `{file, display_name}` + refresh the table. 409 → offer "Overwrite?" (re-POST
  with `force: true`); 422/500 → show the error text.

## API shapes

- Runtime config: `{debug_mode, debug_behavior, debug_notice,
  demo_phone_configured, demo_phone_masked, twilio_number_configured,
  live_vendor_calls_enabled}`. Fetch it
  after auth; it is the only source of truth for execution mode.
- Job: `{id, vertical, area_code, spec: {…domain fields…, existing_quotes?:
  [{company, total, line_items}]}, spec_source, confirmed, discovered_questions:
  [{question, why_it_matters}], documents: [{id, filename, uploaded_at,
  extracted_fields: [str], updates: [{field, from, to}], has_quote,
  insights: [str]}], knowledge_version?, follow_up_plan?, created_at}`
- Intake form: `{vertical, area_code, display_name, spec_schema: {required:
  [str], fields: {name: {type, values?, fields?, item_fields?, default?}}},
  base_questions: [str], learned_questions: [{question, why_it_matters,
  times_seen}]}`
- Call list: `{generated_at, query, state, required_sources, complete, saved,
  provider_status: {source: {status, results?, reason?}}, raw_results, total,
  items: [{name, phone?, address?, city?, rating?, review_count?, sources,
  source_ids?, categories?, url?}]}`
- Call queue: `{debug_mode, debug_behavior, running, summary:
  {current_best_offer: null|{company_id, company_name, quote_id, total, binding,
  red_flags}, offer_range: null|{low, high, count}, called, total, calling,
  excluded_unverified_offers},
  batch: null|{run_id, index, count, size, status, knowledge_version, completed,
  total}, follow_up_plan: [{company_id, company_name, reasons, source_quote_ids,
  knowledge_version, attempts, max_attempts, status}], queue: [{company, status,
  last_call_kind, conversation_id, initial_total, negotiated_total, red_flags, attempt_count,
  recalls_used, recalls_max,
  batch_index, knowledge_version, dial_mode, transcript_kind, follow_up}]}`
- Call record: `{id, company_id, kind, outcome, transcript: [{role, text}],
  transcript_kind: "debug_generated"|"elevenlabs_voice"|"none", has_audio,
  audio_url, attempt_number?, batch_index?, knowledge_version?, mode?}`
- Report: `{job, benchmark: {fair_low, median, fair_high, red_flag_floor},
  market_evidence: [str], ranking: [{company: {name, persona}, outcome,
  initial_total, negotiated_total, final_total, saved_in_negotiation, binding,
  line_items: [{label, code, amount, kind}], red_flags: [{id, severity, label}],
  evidence: [{quote_id, phase, verbatim, conversation_id, call_id,
  verified_in_transcript, kind, audio_url}], score, calls: [{kind, outcome,
  transcript, transcript_kind}]}], recommendation: str}`
- Quotes: `[{company_id, phase: "initial"|"negotiated", total, binding, deposit,
  line_items, red_flags, verbatim_evidence, conversation_id, call_id?,
  knowledge_version?, evidence_kind?, evidence_verified?}]`

## Navigation & quality bar

Left sidebar: logo + wordmark, then Jobs, Domains, Profile. Per-job tab bar:
**Intake, Spec, Call list, Calls, Compare**. After authentication the app shell calls
`GET /api/runtime-config`: when `debug_mode=true`, a persistent global banner above
all routes displays **DEBUG · transcript only** plus `debug_notice`; it is not a
client toggle. When false, show a compact Live outbound status chip. Small reusable
components; loading skeletons; the specified empty states; toasts for every mutation.
