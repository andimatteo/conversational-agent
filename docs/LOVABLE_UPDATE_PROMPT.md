# Lovable UPDATE prompt — for the already-generated QuoteWise app

> **Superseded:** this file contains the retired Call List page. Use
> `docs/LOVABLE_DEMO_COMPLETE_PROMPT.md`.

How to use: paste everything below the `---` into the EXISTING Lovable project's
chat as ONE message, **attaching `assets/logo.png`** to it. It updates the app in
place; the full from-scratch spec stays in `LOVABLE_PROMPT.md`.

---

Update QuoteWise. Keep everything not mentioned here exactly as it is.

**1. Brand & light theme**
- The product name is **QuoteWise** everywhere (browser title, sidebar, login).
- Use the attached image as the ONLY logo — sidebar header 32px beside the
  wordmark, login page 96px, favicon. Never redraw it; it was born on white and
  needs no backing on light surfaces.
- Switch the WHOLE app to a light professional theme in the logo's colors:
  page background `#FFFFFF`, card/section surfaces `#EEF2FF`, hairline borders
  `#C7D2FE`, body text `#312E81`, headings `#1E1B4B`, primary buttons and links
  `#6366F1` (hover `#4338CA`), active/selected states on `#A5B4FC` tints, soft
  shadows, rounded corners, generous whitespace. Keep green/amber/red ONLY for
  status badges and red flags.

**2. Voice intake (new feature)**
- Install the `@elevenlabs/react` npm package.
- On the Intake page, make this the FIRST element: a "🎙 Talk to the estimator"
  card — logo at 48px, subtitle "A 3-minute call fills this whole form", big
  indigo **Start voice intake** button. On click:
  `POST /api/jobs/:jobId/voice-session` (with the Bearer token) →
  `{signed_url, dynamic_variables}` → then
  `useConversation()` + `conversation.startSession({ signedUrl, dynamicVariables })`
  (browser asks for mic permission).
- While connected: pulsing indigo orb (strong pulse when the agent speaks, soft
  when listening), live transcript as chat bubbles from the SDK's `onMessage`
  (agent left, user right), red **End call** button → `conversation.endSession()`.
- On disconnect: toast "Interview saved", refetch the job AND the intake form.
- Errors: mic permission denied → "Allow microphone access to talk to the
  estimator"; 502/503 from voice-session → show the server's message.

**3. The intake form is a live view of `job.spec`**
- Prefill every form field from `job.spec` — the voice call, document uploads
  and the form all edit the SAME spec.
- Whenever a voice call ends or a document is parsed: refetch the job and update
  the form values in place, with a brief lavender flash on each changed field.

**4. Documents UPDATE the intake (not just notes)**
- Document rows from `GET /api/jobs/:jobId/documents` now also carry
  `updates: [{field, from, to}]` — the fields the document CORRECTED. Render
  them as amber diff chips (e.g. "floor: 7 → 2") beside the existing
  `extracted_fields` chips.
- After an upload, toast summarising everything, e.g. "Filled
  property_age_years · updated floor 7 → 2 · +1 competing quote"; refetch the
  job; update the spec view AND the intake form values; show the re-confirm
  banner (the backend resets `confirmed` on any change).
- Keep as-is: dropzone accepting `.pdf .jpg .jpeg .png .webp .txt .md`, the
  "💰 quote on file" badge, the "Leverage on file" card from
  `spec.existing_quotes`, 503 → "Document parsing needs the OpenAI key on the
  server", 415 → list accepted formats.

**5. Sanity checks (fix only if missing)**
- `API_BASE` lives in ONE config file; current value
  `https://partner-may-cheers-switched.trycloudflare.com`.
- Every `/api/*` request carries `Authorization: Bearer <token>`; on 401 clear
  the token and redirect to `/login`. Login page shows the demo hint
  `demo@negotiator.app / demo1234`.
- If the app somehow has no auth yet, add it: `/login` with Sign in / Create
  account tabs (`POST /api/auth/login` and `/api/auth/register`, both return
  `{token, user}`), token in localStorage, avatar menu with Profile
  (`GET /api/me` → `{user, jobs}`) and Logout (`POST /api/auth/logout`).

**6. State-wide provider call list (new feature)**
- Add a Providers page at `/job/:jobId/providers`, linked from each job and from
  the sidebar. On load call `GET /api/jobs/:jobId/call-list`.
- Add state, optional query and target-per-provider. Google Places, Yelp and
  OpenStreetMap are fixed mandatory sources, shown as badges rather than checkboxes.
  "Build call list" sends
  `POST /api/jobs/:jobId/call-list/discover` with
  `{state, query, target_per_provider}` and keeps the old list visible
  while the state-wide search runs.
- Render `provider_status`, `total`, and an `items` table with company, click-to-call
  phone, `sources` chips, rating/reviews, location and external URL. Provider errors
  are partial failures when another source succeeds. Show "3/3 sources complete"
  only when `complete=true`; otherwise identify the missing mandatory source and keep
  the Caller disabled. Partial results have `saved=false` and are diagnostic only.
