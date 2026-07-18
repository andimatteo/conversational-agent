# Lovable prompt — The Negotiator dashboard

Paste the prompt below into Lovable. Set `API_BASE` to your ngrok URL (the FastAPI
server; CORS is already open).

---

Build a clean, trust-focused dashboard called **THE NEGOTIATOR** for a service whose
voice agents phone moving companies, gather itemised quotes, and negotiate. Dark
professional theme, generous whitespace, evidence-first design. All data comes from a
REST API at `API_BASE` (make it a config constant). Poll every 5s where noted. No auth.

### Pages

**1. Job & Spec (`/job/:jobId`)**
- `GET /api/jobs/:jobId` → show the structured job spec (origin/destination cards with
  floor/stairs/elevator/parking, move date, home size, inventory table with `special`
  badges, services toggles, notes).
- Banner states: spec source chips ("voice interview" / "document"), and a big
  **Confirm spec** button → `POST /api/jobs/:jobId/confirm`. Until confirmed, show
  "🔒 Calls locked until you confirm" (the backend enforces it, 409). If confirm
  returns 422, list the missing fields it reports.
- Document upload dropzone → `POST /api/jobs/:jobId/documents` (multipart `file`).
  After upload, re-render the spec and highlight an `existing_quote` key if present:
  "Prior written quote on file — will be used as leverage."

**2. Call Board (`/job/:jobId/calls`)** — poll 5s
- `GET /api/jobs/:jobId/companies` + `/calls` + `/quotes`.
- One row per company: name, persona style tag, latest call kind (quote/negotiate),
  outcome badge (quote=green, callback=amber, decline/hangup=gray), and the running
  quote total. Show transcript in a slide-over panel (turns as chat bubbles,
  role: agent = our negotiator, user = the company rep). If `audio_path` exists,
  note "recording saved".

**3. Comparison (`/job/:jobId/compare`)**
- `GET /api/jobs/:jobId/report`.
- Benchmark strip: fair_low / median / fair_high as a horizontal band chart, each
  company's final_total plotted on it; red_flag_floor marked as a red line.
- Ranked cards from `ranking[]`: rank #, company, score, `initial_total` struck
  through when a `negotiated_total` improved it, "saved $X in negotiation" badge,
  itemised `line_items` table (label / code / amount / kind), red-flag chips with the
  full `label` as tooltip, and an **Evidence** accordion listing `evidence[]`
  (phase, verbatim quote in italics, conversation id in mono).
- Winner card on top with the plain-language `recommendation` text prominent.
- A "Market evidence" footer listing `market_evidence[]` (the documented spread stats).

**4. Market Discovery (`/job/:jobId/market`)**
- Form (city, state) → `GET /api/jobs/:jobId/market?city=&state=` → table of
  discovered real businesses (name, phone, url, snippet) titled "Where the call list
  comes from in the real world". Note: demo calls run against the simulated market.

### Key API shapes
- Report: `{ job, benchmark:{fair_low,median,fair_high,red_flag_floor}, market_evidence:[str],
  ranking:[{company:{name,persona}, outcome, initial_total, negotiated_total, final_total,
  saved_in_negotiation, binding, line_items:[{label,code,amount,kind,contingent}],
  red_flags:[{id,severity,label}], evidence:[{phase,verbatim,conversation_id}], score,
  calls:[{kind,outcome,transcript:[{role,text}],audio_path}]}], recommendation:str }`
- Quotes: `[{company_id, phase:"initial"|"negotiated", total, binding, deposit,
  line_items, red_flags, verbatim_evidence, conversation_id}]`

Empty states matter: before any calls, the Call Board should show "The Caller hasn't
dialed yet"; the Comparison page "No quotes gathered yet."
