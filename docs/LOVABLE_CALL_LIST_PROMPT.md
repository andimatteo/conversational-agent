# Lovable prompt — Call list (market discovery) page

Paste the block below into the existing Lovable project as one message.

---

Add a new per-job page: **Call list** — the step between confirming the spec and
the calls. Update the per-job tab bar to: Intake, Spec, **Call list**, Calls,
Compare. Keep everything else as it is.

**Route: `/job/:jobId/call-list`** (all requests carry the Bearer token)

**Purpose:** build the list of real local businesses from Google Places, Yelp and
OpenStreetMap, then prepare **every callable Google Places vendor** for the
server-side call scheduler.

The authenticated app shell also calls `GET /api/runtime-config`. When
`debug_mode=true`, keep the global banner visible on this page: vendor names and
metadata are real Google data, but bulk calls create labelled transcripts only —
no vendor contact, conversational session, or audio.

**On load:** `GET /api/jobs/:jobId/call-list` — render the last complete saved list
if one exists, else the discovery form with the empty state "No call list yet —
scan the market to find who we should call."

**Discovery form** (card at the top):

- **State** (required): text input, e.g. "North Carolina" or "NC".
- **Search query** (optional): placeholder from the job's trade, e.g. "plumbing
  company" — blank uses the domain default.
- **Results per source** (advanced, collapsed): number input, default 250,
  max 1000. Caption: "higher = broader scan, slower and more API quota".
- **Scan the market** → `POST /api/jobs/:jobId/call-list/discover` with
  `{state, query?, target_per_provider?}`. It can take minutes: retain the old saved
  list while showing an indeterminate "Scanning Google Places, Yelp and
  OpenStreetMap across {state}…" state. On 422/502 show server detail inline.

**Results view** (POST response or saved GET data):

- Header: `total` big, `raw_results` small, state name, generated time, and one
  provider card per `provider_status[source]` using its `{status, results?, reason?}`:
  `ok` green, `skipped` gray with reason, `error` red with reason.
- If `complete=false`, show: "Partial scan — this diagnostic result is not saved
  and cannot replace the last complete call list." Keep it in memory for review,
  but disable vendor preparation. The backend only persists non-empty complete
  scans (`saved=true`).
- Table fields are optional: name, E.164 phone, address/city, rating + reviews,
  source chips, categories, and external URL. Sort rating descending and provide
  client-side name search. Mark a row **"Google · callable"** only when it has a
  phone and `sources` includes `google_places`; all other rows are discovery-only.

**Prepare the complete Google market:**

- For a saved list, compute `N` from all `Google · callable` rows and show one
  primary button **"Use all {N} Google vendors"** →
  `POST /api/jobs/:jobId/companies/from-call-list {count: 0}`.
- `count: 0` is intentional: it means all callable Google vendors. Never use a
  top-N subset, never silently cap the selection, and do not add a fictional-
  company alternative. The endpoint is idempotent and preserves Google identity,
  phone, ratings and discovery IDs.
- Show the response `note` and `debug_mode`, refetch
  `GET /api/jobs/:jobId/call-queue`, then offer **Go to Calls**. Empty Google set:
  "No Google Places vendor with a phone number was found — refine the scan."

Footer note: "Google vendor identities are kept intact. The server decides the
safe execution mode globally and batches all eligible vendors automatically."
