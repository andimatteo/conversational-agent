# Lovable prompt — Call list (market discovery) page

Paste the block below into the existing Lovable project as one message.

---

Add a new per-job page: **Call list** — the step between confirming the spec and
the calls. Update the per-job tab bar to: Intake, Spec, **Call list**, Calls,
Compare. Keep everything else as it is.

**Route: `/job/:jobId/call-list`** (all requests carry the Bearer token)

**Purpose:** build the list of REAL local companies our AI agents would call for
this job, discovered across Google Places, Yelp and OpenStreetMap.

**On load:** `GET /api/jobs/:jobId/call-list` — renders the last saved list if
one exists, else the discovery form with the empty state "No call list yet —
scan the market to find who we should call."

**Discovery form** (card at the top):
- **State** (required): text input, e.g. "North Carolina" or "NC".
- **Search query** (optional): placeholder from the job's trade, e.g. "plumbing
  company" — leave empty to use the default.
- **Results per source** (advanced, collapsed): number input, default 25,
  max 1000. Caption: "higher = broader scan, slower and more API quota".
- **Scan the market** button → `POST /api/jobs/:jobId/call-list/discover`
  `{state, query?, target_per_provider?}`. This can take up to a few minutes:
  show an in-card progress state "Scanning Google Places, Yelp and
  OpenStreetMap across {state}…" (indeterminate bar, disable the button).
  On 422/502 show the server's error text inline.

**Results view** (from the POST response, or the saved GET data):
- Header row: `total` big ("34 companies"), `raw_results` small ("from 84 raw
  results, deduplicated"), state name, and one chip per entry of
  `provider_status`: value "ok" → green chip; "skipped" → gray chip with
  tooltip "API key not configured on the server"; anything else → red chip
  showing the text.
- If `complete` is false: amber banner "Partial scan — some sources were
  unavailable, so this list is NOT saved on the job. Re-scan when all sources
  are configured." (the backend only persists complete scans; keep partial
  results rendered from the POST response in memory).
- **Table of `items`** (each field may be absent — hide empty cells):
  name, phone (mono, E.164), address + city, rating ("4.8 ★ · 120 reviews"
  from rating + review_count), sources chips (google_places / yelp /
  openstreetmap), categories as tiny tags, external-link icon on `url`.
  Sort by rating desc by default; client-side search box filtering by name.
- Footer note: "These are discovered leads. Demo calls run against the
  simulated market — real outbound dialing is the next step."
