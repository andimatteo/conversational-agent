# Lovable prompt — QuoteWise dashboard

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
  default `https://point-tears-childrens-residential.trycloudflare.com`.
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

### 4. Call board — `/job/:jobId/calls` (poll 5s)
`GET /api/jobs/:jobId/companies` + `/calls` + `/quotes`.
One row per company: name, persona style tag, latest call kind (quote/negotiate),
outcome badge (quote=green, callback=amber, decline/hangup=gray), running quote
total. Transcript in a slide-over: chat bubbles, role "agent" = our negotiator,
"user" = the company rep; note "recording saved" when `audio_path` exists.
Empty state: "The Caller hasn't dialed yet."

### 5. Comparison — `/job/:jobId/compare`
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

### 6. Domains — `/domains`
- `GET /api/verticals` → table: display_name, vertical, area_code, file, valid ✓/✗.
- "**Generate a domain sheet with AI**" card: vertical (slug), area_code, notes
  (textarea) → `POST /api/verticals/generate {vertical, area_code, notes}` →
  spinner "The AI is writing the config sheet… (~1 min)" → on success show
  `{file, display_name}` + refresh the table. 409 → offer "Overwrite?" (re-POST
  with `force: true`); 422/500 → show the error text.

## API shapes

- Job: `{id, vertical, area_code, spec: {…domain fields…, existing_quotes?:
  [{company, total, line_items}]}, spec_source, confirmed, discovered_questions:
  [{question, why_it_matters}], documents: [{id, filename, uploaded_at,
  extracted_fields: [str], updates: [{field, from, to}], has_quote,
  insights: [str]}], created_at}`
- Intake form: `{vertical, area_code, display_name, spec_schema: {required:
  [str], fields: {name: {type, values?, fields?, item_fields?, default?}}},
  base_questions: [str], learned_questions: [{question, why_it_matters,
  times_seen}]}`
- Report: `{job, benchmark: {fair_low, median, fair_high, red_flag_floor},
  market_evidence: [str], ranking: [{company: {name, persona}, outcome,
  initial_total, negotiated_total, final_total, saved_in_negotiation, binding,
  line_items: [{label, code, amount, kind}], red_flags: [{id, severity, label}],
  evidence: [{phase, verbatim, conversation_id}], score, calls: [{kind, outcome,
  transcript: [{role, text}]}]}], recommendation: str}`
- Quotes: `[{company_id, phase: "initial"|"negotiated", total, binding, deposit,
  line_items, red_flags, verbatim_evidence, conversation_id}]`

## Navigation & quality bar

Left sidebar: logo + wordmark, then Jobs, Domains, Profile. Per-job tab bar:
Intake, Spec, Calls, Compare. Small reusable components; loading skeletons; the
specified empty states; toasts for every mutation.
