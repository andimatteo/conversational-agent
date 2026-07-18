# Lovable prompt — The Negotiator dashboard

Paste everything below the `---` into a NEW Lovable project (lovable.dev).
`API_BASE` is pre-filled with the current cloudflared tunnel; if the tunnel
restarts, change the constant in Lovable (it's told to keep it editable).
The FastAPI server + tunnel must be running; CORS is open, no auth.

---

Build a clean, trust-focused dashboard called **THE NEGOTIATOR** for a service whose
voice agents interview a customer, phone local companies for itemised quotes, and
negotiate on the customer's behalf. The product is domain-agnostic (plumbing today,
any trade tomorrow) — never hardcode plumbing-specific fields; always render from the
API's schemas. Dark professional theme, generous whitespace, evidence-first design.

All data comes from a REST API. Define `const API_BASE` in ONE config file, easy to
edit, default: `https://point-tears-childrens-residential.trycloudflare.com`
Poll every 5s where noted. No auth. Handle fetch errors with a small toast + retry.

### Pages

**1. Jobs (`/`)**
- `GET /api/jobs` → list, newest first: job id (mono), vertical + area_code chips,
  `confirmed` badge (green "confirmed" / amber "awaiting confirmation"), created_at,
  spec_source chips ("interview" / "document" / "form" / "sample").
- **New job** button → modal: `GET /api/verticals` → pick a domain/area
  (display_name, vertical, area_code, `valid` check) → `POST /api/jobs`
  `{vertical, area_code}` → go to the intake form.

**2. Intake form (`/job/:jobId/intake`)**
- `GET /api/intake-form?vertical=<job.vertical>&area_code=<job.area_code>` →
  `{spec_schema, base_questions, learned_questions}`.
- Render input fields FROM `spec_schema.fields` (this makes the form domain-agnostic):
  `{type: enum, values}` → select; `bool` → toggle; `int`/`number` → number input;
  `str` → text; `date` → date picker; `object` → grouped fieldset of its `fields`;
  `list` → repeatable rows of its `item_fields`. Mark `spec_schema.required` fields
  with *. Show `base_questions[i]` as helper text above the matching fields
  (best-effort pairing; leftover questions go in a "The estimator would also ask"
  info panel).
- **Learned questions section**: for each of `learned_questions`
  (`{question, why_it_matters, times_seen}`) render a highlighted card with a
  "📚 learned from calls" badge, the question as a free-text input, `why_it_matters`
  as tooltip, "seen ×N" tag. Answers get appended into the spec's `notes` field as
  "Q: … A: …" lines. Empty state: "No learned questions in this area yet — calls
  will teach the form."
- Save → `PUT /api/jobs/:jobId/spec` with `{spec}` → response has
  `missing_required_fields`: if non-empty, highlight those fields and list them in
  an amber banner; if empty, show the **Confirm spec** call-to-action.
- Note under the form: "You can also fill this by voice — the AI estimator
  interviews you and the same form updates."

**3. Job & Spec (`/job/:jobId`)**
- `GET /api/jobs/:jobId`. Render the spec generically: one card per top-level key
  (objects → key/value grid, arrays → table, scalars → labelled value). NO
  domain-specific layout.
- Big **Confirm spec** button → `POST /api/jobs/:jobId/confirm`. Until confirmed show
  "🔒 Calls locked until you confirm" (backend enforces with 409/422 — on 422 list
  the missing fields it returns). Any spec edit resets confirmation (the API does
  this; reflect it).
- If `discovered_questions` is non-empty, show a panel "🧠 What this job taught the
  intake form" listing `{question, why_it_matters}` — these auto-join future forms
  in this area.
- Document upload dropzone → `POST /api/jobs/:jobId/documents` (multipart `file`).
  After upload re-render; if the spec gains `existing_quote`, banner: "Prior written
  quote on file — will be used as leverage."

**4. Call Board (`/job/:jobId/calls`)** — poll 5s
- `GET /api/jobs/:jobId/companies` + `/calls` + `/quotes`.
- One row per company: name, persona style tag, latest call kind (quote/negotiate),
  outcome badge (quote=green, callback=amber, decline/hangup=gray), running quote
  total. Transcript in a slide-over (chat bubbles; role "agent" = our negotiator,
  "user" = the company rep). If `audio_path` exists note "recording saved".
- Empty state: "The Caller hasn't dialed yet."

**5. Comparison (`/job/:jobId/compare`)**
- `GET /api/jobs/:jobId/report`.
- Benchmark strip: `benchmark.fair_low / median / fair_high` as a horizontal band,
  each company's `final_total` plotted on it; `red_flag_floor` as a red line.
- Ranked cards from `ranking[]`: rank #, company, score, `initial_total` struck
  through when `negotiated_total` improved it, "saved $X in negotiation" badge,
  `line_items` table (label/code/amount/kind), red-flag chips (full `label` as
  tooltip), **Evidence** accordion of `evidence[]` (phase, verbatim quote in
  italics, conversation id in mono).
- Winner card on top with the plain-language `recommendation` prominent.
- Footer "Market evidence": `market_evidence[]`.
- Empty state: "No quotes gathered yet."

**6. Domains (`/domains`)**
- `GET /api/verticals` → table: display_name, vertical, area_code, file, valid ✓/✗.
- "**Generate a domain sheet with AI**" card: inputs vertical (slug), area_code,
  notes (textarea) → `POST /api/verticals/generate` `{vertical, area_code, notes}` →
  spinner ("The AI is writing the config sheet…", can take ~1 min) → success: show
  returned `{file, display_name}` and refresh the table. On 409 offer "Overwrite?"
  (re-POST with `force: true`); on 422/500 show the error text (server needs an
  OpenAI key).

### Key API shapes
- Job: `{id, vertical, area_code, spec:{...domain-specific...}, spec_source,
  confirmed, discovered_questions:[{question,why_it_matters}], created_at}`
- Intake form: `{vertical, area_code, display_name, spec_schema:{required:[str],
  fields:{name: {type, values?, fields?, item_fields?, default?}}},
  base_questions:[str], learned_questions:[{question, why_it_matters, times_seen}]}`
- Report: `{job, benchmark:{fair_low,median,fair_high,red_flag_floor},
  market_evidence:[str], ranking:[{company:{name,persona}, outcome, initial_total,
  negotiated_total, final_total, saved_in_negotiation, binding, line_items:[{label,
  code,amount,kind}], red_flags:[{id,severity,label}], evidence:[{phase,verbatim,
  conversation_id}], score, calls:[{kind,outcome,transcript:[{role,text}]}]}],
  recommendation:str}`
- Quotes: `[{company_id, phase:"initial"|"negotiated", total, binding, deposit,
  line_items, red_flags, verbatim_evidence, conversation_id}]`

Navigation: left sidebar (Jobs, Domains) + per-job tabs (Intake, Spec, Calls,
Compare). Keep components small; empty states everywhere; every dollar amount
formatted `$1,234`.
