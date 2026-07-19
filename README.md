# QUOTEWISE

<img src="assets/logo.png" width="160" alt="QuoteWise — crystal ball with a phone"/>

Voice agents that build one confirmed job specification, contact a market, compare
itemised quotes, and negotiate with evidence. The MVP domain is **residential
plumbing**; `verticals/moving.yaml` demonstrates that the same orchestration can be
retargeted through configuration.

## The loop

```text
01 ESTIMATOR             02 CALLER                         03 CLOSER
voice interview ─┐       every Google vendor ─┐            verified own history
                 ├─► one confirmed spec       ├─► quotes ─► + frozen competing bids
document intake ─┘       sqrt(n) batches ─────┘            + ranked evidence/report
```

## What the application does

QuoteWise closes the complete purchasing loop for services whose real price is only
available by phone. A customer creates a job, uploads a PDF, image, quote or inventory,
and completes a short browser voice interview. Both inputs merge into one structured
specification; the customer must review and confirm it before telephony is unlocked.

After the final review, one launch request performs a fresh Google Places API search,
promotes every callable Places result,
lead, gathers comparable itemised quotes, learns new price-sensitive questions from each
conversation, and plans evidence-backed follow-ups. The Calls workspace shows progressive
transcripts, current best offer, observed range, called/total, batch barriers and knowledge
versions. The Compare workspace ranks offers using price, binding status, itemisation and
red flags, with direct links to transcript passages and recordings. Compare also renders
every geolocated offer on a price-pin map and marks the backend recommendation with a
star. A final Closer call can use genuine competing evidence to negotiate price, fees or terms.

The MVP is configured for residential plumbing, including a resettable water-heater demo.
The engine itself is domain-independent: changing the vertical YAML replaces the job schema,
questions, fee taxonomy, benchmarks, red flags, negotiation levers and disclosure rules
without rewriting the agents or scheduler.

## Technology stack

| Layer | Technology | Responsibility |
|---|---|---|
| Web product | Lovable-generated React + TypeScript | Authenticated Intake, Spec, Calls and Compare workspaces; realtime polling, price-pin map and protected audio playback |
| API and orchestration | Python, FastAPI, Pydantic, Uvicorn | Product API, validation, agent tools, background campaigns, safety gates and provider reconciliation |
| Voice agents | ElevenLabs Agents Platform | Estimator, Caller and Closer conversations, dynamic variables, tool calls, transcripts and recordings |
| Telephony | Twilio number imported into ElevenLabs | Outbound live voice transport for authorized real calls and the allow-listed human demo |
| Document intelligence | OpenAI multimodal API | Extracts structured scope, equipment details and existing quotes from PDFs, images and text |
| Market discovery | Google Places, Yelp and OpenStreetMap adapters | Finds, normalizes and deduplicates local counterparties; Google identities remain the scheduled source of truth |
| Persistence | SQLite in WAL mode with JSON records | Jobs, companies, calls, quotes, batches, run leases, sessions, learnings and recall reservations |
| Domain configuration | YAML + JSON Schema | Vertical-specific intake fields, prompts, benchmarks, red flags, fees and negotiation policy |
| Verification | Offline Python integration and regression suites | Auth isolation, document merging, batch barriers, grounding, provider uncertainty, reset and recall limits |

## Architecture and information flow

```text
Documents ──► OpenAI parser ─┐
                             ├─► validated spec ─► user confirmation
Voice intake ─► Estimator ───┘                         │
                                                      ▼
Final review ─► live Google Places API ─► normalized vendors ─► sqrt(n) quote batches
                                              │
                    frozen knowledge vN ◄─────┤ each agent retrieves:
                                              │ spec + benchmark + own history
                                              │ + allowed competitive quote IDs
                                              ▼
                              hard terminal barrier for the whole batch
                                              │
                 transcript + quote validation + learning persistence
                                              │
                                      knowledge vN+1
                                              │
                         final Closer retrieval and grounded negotiation
                                              │
                              ranked evidence-backed comparison
```

### Agents and retrieval

- **Estimator:** calls `get_intake_form` to retrieve fields already present, missing
  required fields and questions learned for the same vertical and area. It asks only
  for gaps, then saves into the same spec used by documents and the web form.
- **Caller:** retrieves the immutable confirmed scope and a batch-scoped knowledge
  snapshot. During the first pass it explores the vendor's price and policies; it is
  explicitly forbidden from negotiating or inventing competitor leverage.
- **Closer:** retrieves the vendor's verified own quote history plus the exact set of
  eligible competing quote IDs. It can request a price match, remove fees or improve
  terms, but it cannot cite anything outside that frozen context.
- **Counterparty agents:** optional ElevenLabs agents provide distinct simulated market
  behaviors for testing. Debug-scale conversations instead use clearly labelled,
  transcript-only records and never contact the displayed Google business.

Retrieval is implemented through authenticated agent tools over the FastAPI backend,
not by placing an unconstrained conversation history in the prompt. Each call receives
only the confirmed specification, current benchmark, its own history and competitive
claims that passed the evidence gates.

### Validation and anti-hallucination controls

Validation happens before, during and after every call:

1. Pydantic and the vertical schema sanitize document, form and voice fields. Any edit
   clears confirmation; calls remain locked until the complete spec is confirmed again.
2. The scheduler deep-copies the spec and hashes it for the run. Every member of one
   batch receives the same immutable knowledge version.
3. Structured call outcomes require an itemised quote, callback commitment, decline or
   documented failure. Quote totals are checked against their line items and benchmark
   red-flag rules.
4. Post-call grounding correlates money, company references and `leverage_quote_ids`
   with the frozen snapshot. New quote facts must occur in counterparty transcript turns,
   not only in an agent tool call.
5. Synthetic demo leverage is valid only when the agent calls it a **simulated
   demo-market offer** in the same turn. It can never be presented as a real Google
   business quote.
6. Provider timeouts fail closed: uncertain external state suppresses automatic redial.
   Persistent leases prevent duplicate campaigns, and every vendor is limited to at most
   two recalls per job, including failed or reserved attempts.

### Synchronous batch calling and progressive knowledge

For `n` eligible vendors, the server computes `ceil(sqrt(n))` as concurrency and divides
the complete market into ordered batches. Calls inside a batch run concurrently, while a
hard barrier waits until all of them are terminal. Only then are transcripts, verified
quotes and learned questions published into a new knowledge version. Consequently, no
agent can accidentally use a result that was still in flight or learned by a peer in the
same batch.

The resettable demo places the consenting human in quote batch one as an Explorer. The
remaining market produces progressive synthetic conversations from the same durable Call
records used to create its quotes. After the last quote barrier, the backend automatically
creates one final callback batch. The Closer receives the human's initial quote plus all
eligible offers gathered in the completed batches and can negotiate against that grounded
context. One explicit UI consent authorizes exactly these two live calls; no Google vendor
phone crosses the provider boundary.

Everything domain-specific—spec schema, intake questions, benchmark, fee taxonomy,
red flags, negotiation levers, and disclosure policy—lives in `verticals/*.yaml`.

The scheduler freezes the confirmed spec for the whole run. For `n` eligible vendors,
it executes batches of `ceil(sqrt(n))` concurrent calls. Every member of a batch sees
the same knowledge snapshot; the next snapshot is published only after every call in
the current batch reaches a terminal state. Competitive claims are correlated to exact
quote IDs, and each vendor's prior offers are included as separate verified history.

## Safe debug mode and live voice

`DEBUG_CALLS=true` is the default and the recommended mode for development and the
scale portion of the demo.

| Path | Destination | Transcript | Audio | Purpose |
|---|---|---|---|---|
| Bulk `/calls/start`, debug on | No phone call | Explicitly labelled synthetic | None | Safely exercise every real Google vendor identity, batching, quotes, learning, and follow-ups |
| Prepared demo `/launch` | Fresh live Places discovery, then only the allow-listed human is called twice; every Google phone remains untouched | N-1 labelled synthetic + two live role-play transcripts | Live role-play recordings when available | One reviewed action discovers, starts batch 1 exploration, then automatically negotiates after all barriers |
| Bulk `/calls/start`, debug off | Each vendor's Google phone number | ElevenLabs voice transcript | Recording when available | Real authorized market calling only |
| Legacy manual `/calls/demo` | Same server-side `DEMO_PHONE_NUMBER` | ElevenLabs voice transcript | Recording when available | Explicit non-campaign rehearsal only |

Debug bulk execution does **not** dial, create an ElevenLabs conversation, generate
audio, or run counter-agents. Company name, phone, and Google Place identity remain the
real discovery record; only the transcript and structured result are generated.
`GET /api/runtime-config` is the backend-authoritative source for the UI mode banner.

An explicitly prepared role-play job is the narrow exception while debug is enabled.
After review, `/launch` performs a new Google Places request, promotes every callable
result and associates one identity with the allow-listed `DEMO_PHONE_NUMBER` in quote
batch one; every other row remains
transcript-only. The selected company's stored name, Google phone and Place ID are not
changed, and the opening disclosure states that the consenting human does not represent
that business. After all quote barriers, the same run automatically calls the human back
for grounded negotiation. Neither endpoint accepts an arbitrary destination. Bulk real-vendor
telephony additionally requires `LIVE_VENDOR_CALLS_ENABLED=true`; turning debug off
alone can never start calling real businesses.

> The hybrid flow is verified offline at the provider boundary. Twilio connectivity,
> the live concession and three distinct live negotiation styles still need recorded
> rehearsal evidence before they can be claimed to judges.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Important `.env` settings:

```dotenv
VERTICAL=plumbing
DEBUG_CALLS=true
LIVE_VENDOR_CALLS_ENABLED=false
ELEVENLABS_API_KEY=
OPENAI_API_KEY=
GOOGLE_PLACES_API_KEY=
YELP_API_KEY=
PUBLIC_BASE_URL=https://YOUR-TUNNEL.example
ELEVENLABS_PHONE_NUMBER_ID=
DEMO_PHONE_NUMBER=
AGENT_TOOL_SECRET=
```

- Keep `DEBUG_CALLS=true` for transcript-only bulk runs.
- Keep `LIVE_VENDOR_CALLS_ENABLED=false` unless real-business calling has been
  explicitly approved. It is a second gate and is not needed for `/calls/demo`.
- `DEMO_PHONE_NUMBER` is the single authorized human destination; keep its real value
  only in `.env`.
- `ELEVENLABS_PHONE_NUMBER_ID` is the Twilio number imported under ElevenLabs **Phone
  Numbers**.
- Restart the API after changing environment values. Re-run provisioning after prompt,
  tool, vertical, or `PUBLIC_BASE_URL` changes.

```bash
uvicorn negotiator.server:app --port 8000
ngrok http 8000
python -m agents.provision
```

## Low-level transcript-only API run

All product endpoints require `Authorization: Bearer <token>` unless documented
otherwise.

These compatibility endpoints remain useful for ordinary jobs and diagnostics; the
prepared demo UI intentionally removes the Call List page and uses the atomic `/launch`
flow documented below.

1. Create a job, complete voice/document intake, and confirm the spec.
2. Discover the market with `POST /api/jobs/{job_id}/call-list/discover`.
3. Promote **all** callable Google Places results. Omitting `count`, or sending `0`,
   means all vendors:

```http
POST /api/jobs/{job_id}/companies/from-call-list
{}
```

4. Start quote gathering. `parallel` is deprecated and ignored; the server always
   computes the batch policy:

```http
POST /api/jobs/{job_id}/calls/start
{"phase":"quote","idempotency_key":"ui-generated-uuid"}
```

5. Poll `GET /api/jobs/{job_id}/call-queue`. Its summary contains the current
   risk-adjusted best offer, observed offer range, and `called/total`; `batch` exposes
   index, size, completion barrier, and knowledge version.
6. Inspect `GET /api/jobs/{job_id}/follow-ups`. Recommendations are explainable and do
   not dial automatically. Run selected recalls with either explicit `company_ids` or:

```http
POST /api/jobs/{job_id}/calls/start
{"phase":"negotiate","recommended_only":true}
```

Every terminal vendor call performs a backend learning pass, even if the agent omitted
its logging tool. Price-relevant questions derived from contingent fees, conditions,
and vendor transcript evidence are deduplicated by `(vertical, area_code)`, retain call
and company provenance, appear on the job when new, and feed future intake forms.
Any second or later attempt to the same vendor is a recall. At most **two recalls per
job/vendor** are allowed across quote retries, negotiations, and live-demo calls;
reserved, running, failed, and completed attempts all consume a slot.

## Resettable hybrid live demo

Prepare one fresh, auditable demo job without deleting prior evidence:

```bash
python -m negotiator.demo_reset
# or choose the consenting role-player identity explicitly
python -m negotiator.demo_reset --live-vendor "Exact Google vendor name"
```

The command archives prior demo jobs only after the replacement is ready, preserves
their calls, quotes, batches, claims, recalls, recordings and uploads, and never starts
a call or performs discovery. The Spec review sends one atomic launch request:

```http
POST /api/jobs/{job_id}/launch
{"idempotency_key":"fresh-uuid","authorize_demo_calls":true}
```

The endpoint calls Google Places live, promotes every callable result, selects the
role-play identity and immediately starts the campaign. For ten vendors the quote
phase is 4/4/2: the selected human is in batch one and
nine rows are synthetic transcript-only. A final single-company batch is appended
automatically for the grounded callback after every quote barrier.

The Closer may cite a debug-generated amount only as an exact **simulated demo-market
offer**, never as a real quote from the displayed Google business. The response masks
the configured destination. Poll the call queue until terminal, then use:

- `GET /api/jobs/{job_id}/calls` for transcript metadata and `audio_url`;
- `GET /api/jobs/{job_id}/calls/{call_id}/audio` for authenticated MP3 playback;
- `GET /api/jobs/{job_id}/report` for ranked, transcript-verified evidence.

Debug calls intentionally have no audio and the audio endpoint returns 404 for them.
Both live stages use ElevenLabs native batch calling, with terminal recipient tracking
and cancellation without exposing a destination field to the browser. See
`docs/DEMO_SCRIPT.md` for the exact run-of-show and its evidence limits.

## Offline verification

```bash
make test

# Or run a focused suite:
python -m tests.smoke_test
python -m tests.estimator_test
python -m tests.documents_test
python -m tests.market_discovery_test
python -m tests.callqueue_test
python -m tests.debugcalls_test
python -m tests.batching_test
python -m tests.demo_campaign_test
python -m tests.demo_reset_test
python -m tests.learnings_test
python -m tests.spec_validation_test
python -m tests.auth_test
python -m tests.runclaims_test
python -m tests.recall_limits_test
python -m tests.evidence_test
python -m tests.provider_status_test
```

`make test` runs the full offline suite. The debug, batching and hybrid campaign tests
assert no accidental vendor telephony, deterministic distinct styles, `10 → 4/4/2`
batching, hard barriers, frozen knowledge, one allow-listed provider destination,
truthful simulated-leverage disclosure, grounded recalls, non-destructive reset, the
mandatory learning pass, atomic run ownership, provider-state reconciliation, and the
two-recall hard cap. They do not prove Twilio connectivity or live conversation quality.

## Repository map

```text
verticals/                 domain sheets: schemas, benchmarks, red flags, levers
agents/                    generated prompts, personas, ElevenLabs provisioning
market_discovery/          Google Places + Yelp + OSM discovery and normalization
negotiator/debugcalls.py   deterministic transcript-only vendor simulation
negotiator/demo_reset.py   non-destructive preparation of a resettable hybrid demo
negotiator/callrunner.py   all-vendor scheduler, batches, barriers, debug/live modes
negotiator/knowledge.py    frozen snapshots, grounded history, follow-up planning
negotiator/evidence.py     post-call monetary/competitor grounding validation
negotiator/runclaims.py    atomic run leases, idempotency and crash fencing
negotiator/recall_limits.py persistent maximum-two recall guard
negotiator/learnings.py    mandatory post-call question extraction and persistence
negotiator/report.py       ranking and transcript/audio evidence
negotiator/server.py       authenticated product API and agent webhooks
simulation/                ElevenLabs audio bridge, mic mode, transcript/audio finalizer
docs/DEMO_SCRIPT.md        judge-facing debug/live run-of-show and proof checklist
```
