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
| Bulk `/calls/start`, debug off | Each vendor's Google phone number | ElevenLabs voice transcript | Recording when available | Real authorized market calling only |
| Explicit `/calls/demo` | Server-side `DEMO_PHONE_NUMBER` only | ElevenLabs voice transcript | Recording when available | Human-in-the-loop qualifying demo through Twilio |

Debug bulk execution does **not** dial, create an ElevenLabs conversation, generate
audio, or run counter-agents. Company name, phone, and Google Place identity remain the
real discovery record; only the transcript and structured result are generated.
`GET /api/runtime-config` is the backend-authoritative source for the UI mode banner.

`POST /api/jobs/{job_id}/calls/demo` is the sole deliberate exception while debug is
enabled. It cannot accept an arbitrary destination: the backend always dials the one
allow-listed `DEMO_PHONE_NUMBER`, using the imported Twilio number in ElevenLabs, while
the selected Google company supplies the vendor identity and grounded quote history.
Bulk vendor telephony additionally requires `LIVE_VENDOR_CALLS_ENABLED=true`; turning
debug off alone can never start calling real businesses.

> The debug flow, including three deterministic styles, is verified offline. The
> Twilio demo endpoint and the required live sequence of three distinct calls plus one
> measurable concession still need to be rehearsed and proven before presentation.

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

## Transcript-only end-to-end run

All product endpoints require `Authorization: Bearer <token>` unless documented
otherwise.

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

## Allow-listed live demo

Use a fresh job so qualifying live evidence is not mixed with debug-generated quotes.
After configuring and provisioning Twilio/ElevenLabs, select a Google company already
attached to the job:

```http
POST /api/jobs/{job_id}/calls/demo
{"company_id":"co_example","phase":"quote"}
```

The response masks the configured destination. Poll the call queue until terminal,
then use:

- `GET /api/jobs/{job_id}/calls` for transcript metadata and `audio_url`;
- `GET /api/jobs/{job_id}/calls/{call_id}/audio` for authenticated MP3 playback;
- `GET /api/jobs/{job_id}/report` for ranked, transcript-verified evidence.

Debug calls intentionally have no audio and the audio endpoint returns 404 for them.
The demo itself is submitted as an ElevenLabs native batch with one recipient, which
provides terminal recipient tracking and cancellation without exposing a destination
field to the browser.
See `docs/DEMO_SCRIPT.md` for the qualifying three-call plus concession checklist.

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
python -m tests.learnings_test
python -m tests.spec_validation_test
python -m tests.auth_test
python -m tests.runclaims_test
python -m tests.recall_limits_test
python -m tests.evidence_test
python -m tests.provider_status_test
```

`make test` runs the full offline suite. The debug and batching tests assert no telephony/audio, deterministic distinct styles,
`10 → 4/4/2` batching, hard batch barriers, frozen knowledge, grounded recalls, the
mandatory learning pass, atomic run ownership, provider-state reconciliation, and the
two-recall hard cap. They do not prove Twilio connectivity or live conversation
quality.

## Repository map

```text
verticals/                 domain sheets: schemas, benchmarks, red flags, levers
agents/                    generated prompts, personas, ElevenLabs provisioning
market_discovery/          Google Places + Yelp + OSM discovery and normalization
negotiator/debugcalls.py   deterministic transcript-only vendor simulation
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
