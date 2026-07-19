# CLAUDE.md — session handoff for QuoteWise (ElevenLabs "The Negotiator" challenge)

Claude Code loads this file automatically at the start of every session. It is the
single source of truth for project state; update it when state changes.

## What this is

Hack-Nation 6th Global AI Hackathon, **ElevenLabs "The Negotiator" challenge**:
voice agents that phone a market, gather comparable itemised quotes, and negotiate.
Full brief context and judging criteria: see `docs/DEMO_SCRIPT.md` (each demo beat
maps to a success criterion).

Decisions (made 2026-07-18, do not re-litigate):
- **The product is domain-agnostic via "domain sheets"** (`verticals/*.yaml`, one per
  vertical+area, AI-writable). **MVP domain: plumbing** (sheet `verticals/plumbing.yaml`,
  area 28202). Moving stays as the second sheet proving the swap.
- **Counterparties: agent-to-agent** (3 personas with hidden pricing policies)
  **+ human-in-the-loop mode** (`--human`, user answers via mic). Twilio = stretch only.
- **Stack: Python/FastAPI backend + Lovable frontend** (prompt in `docs/LOVABLE_PROMPT.md`).
- Credits available: ElevenLabs, OpenAI (vision doc parsing + sheet generation),
  Google Places and Yelp (call-list discovery); OSM/Overpass needs no API key.

## Architecture in one paragraph

`verticals/<domain>[-<area>].yaml` is a **domain sheet** — estimator persona, base
form questions, spec schema, benchmark price model (generic `rate_card`: callout +
hours×rate + parts + declarative modifiers; moving keeps its bespoke crew model),
red-flag rules, fee taxonomy, negotiation levers, honesty policy.
`negotiator/packs.py` loads/validates sheets by (vertical, area) with fallback to
the domain base sheet; `negotiator/packgen.py` lets the AI WRITE a new sheet
(OpenAI, validated, `python -m negotiator.packgen --vertical hvac --area 28203` or
`POST /api/verticals/generate`). The intake form = sheet base questions **+
learned questions** (`learned_questions` table, per vertical+area). Decision
(Andrea, 2026-07-18): new price factors are discovered on VENDOR calls (caller
phase — `log_learned_questions` wiring there is TODO), NEVER asked of the
customer; the estimator only CONSUMES the pool via `get_intake_form`. Learned
questions surface to the user on the job and join every future form in that area
(`GET /api/intake-form`).
`agents/prompts.py` GENERATES all system prompts from the sheet (estimator /
caller / closer + one per counterparty persona in `agents/personas.yaml`).
`agents/provision.py` upserts 6 agents + 7 webhook tools to ElevenLabs (ids cached
in `agents/registry.json`, gitignored). Mid-call, agents hit `/agent-tools/*`
webhooks on the FastAPI server (`negotiator/server.py`), which writes SQLite
(`data/negotiator.db`, gitignored). **Honesty is architectural**: the closer's only
source of competing bids is the `get_competing_quotes` webhook (reads the real
quote DB); counterparties get hidden ground-truth pricing from `counterparty_pricing`
(only tool they have). Agent-to-agent calls run over `simulation/bridge.py`: two
live ElevenLabs `Conversation` sessions with custom `AudioInterface`s piping
real-time-paced pcm_16000 into each other (real barge-in; `interrupt()` flushes).

## Current state (2026-07-18)

DONE and verified offline:
- Full backend loop passes `.venv/bin/python -m tests.smoke_test` (moving, no API keys):
  spec-confirmation guard (409 before confirm), red-flag engine (lowballer trips all 5),
  leverage gate (excludes company being called), report ranking (negotiated binding
  price-match wins, flagged-cheapest ranks last, $876 negotiation delta).
- **Estimator module is domain-sheet-driven** and passes `.venv/bin/python -m tests.estimator_test`
  (plumbing, no API keys): sheet load/validate + area fallback, per-(vertical,area) jobs,
  rate-card benchmark with modifiers, learned-questions loop (log → dedupe/times_seen →
  next form in same area includes them → isolated from other domains), red flags on the
  plumbing pack. Both estimator prompts (plumbing/moving) render with the new
  get_intake_form / log_learned_questions tooling.
- All modules import against elevenlabs SDK **2.58.0**; `AudioInterface` contract
  (start/stop/output/interrupt) and `ConversationInitiationData(dynamic_variables=...)`
  confirmed against installed SDK. Prompts render `{{job_id}}` etc. correctly.
- `agents/provision.py` now provisions 9 tools (added get_intake_form,
  log_learned_questions); estimator owns both plus save_job_spec. **Never run live yet.**
- `negotiator/packgen.py` (AI sheet writer) is code-complete but untested live.
- **Browser voice intake**: `POST /api/jobs/{id}/voice-session` (owner-scoped)
  returns an ElevenLabs signed wss URL + `{job_id}` dynamic variables; the Lovable
  frontend uses `@elevenlabs/react` `startSession({signedUrl, dynamicVariables})`
  with the user's mic. Endpoint live-tested (real signed URL returned); the full
  browser call is untested until the Lovable app exists. CLI `run_intake` remains
  the fallback path.
- **Document intake works LIVE** (verified with a real OpenAI parse): PDFs/photos/text
  via `POST /api/jobs/{id}/documents` — schema-driven retrieval against the job's pack,
  merged into the call's spec: docs fill gaps AND can UPDATE fields already on file
  (parser emits corrections only when the document is more authoritative; every change
  tracked as a `{field, from, to}` diff on the document record for the frontend),
  quotes accumulate in `spec.existing_quotes` as closer leverage, insights append to
  notes, any change resets confirmation. Files land in `data/uploads/{job_id}/`;
  `GET .../documents` lists them. Offline test: `python -m tests.documents_test`
  (parser mocked). PDF path uses OpenAI file content parts — live-tested only with
  text so far.
- **State-wide call-list discovery** is isolated in `market_discovery/`: Google
  Places + Yelp + OSM/Overpass adapters, full-state grid/boundary search, E.164 phone
  normalization, cross-source dedupe and partial-failure handling. Results are cached
  separately on the job (not in `companies`, so uncalled leads do not pollute reports).
  Frontend contract: `POST /api/jobs/{id}/call-list/discover` and
  `GET /api/jobs/{id}/call-list`. Offline service + authenticated endpoint test:
  `python -m tests.market_discovery_test`.

- **THE CALLER MODULE IS LIVE-PROVEN** (2026-07-18 evening): server-side call
  orchestration in `negotiator/callrunner.py` (`POST /api/jobs/{id}/calls/start`,
  `GET /api/jobs/{id}/call-queue` — statuses to_call/queued/calling/quote/callback/
  decline/hangup derived from real DB records). First successful live agent-to-agent
  bridge call: Caller vs plumbing lowballer — AI disclosure, in-character robot
  reaction, hidden-fee extraction (anchor $1,400 → real all-in $2,572 itemised in 5
  codes, non_binding flag, verbatim evidence, outcome=quote, transcript+recording
  fetched). Bridge deadlock FIXED in `simulation/bridge.py`: speech is held until the
  peer connects (the greeting was being dropped) and the pump sends continuous
  zero-frames as silence so ASR can close turns. `conversation_id` read from
  `_conversation_id`. Report/queue use the LATEST initial quote (callers refine
  mid-call).
- **Counterparty personas are per-domain**: `agents/personas/{plumbing,moving}.yaml`
  (3 styles each, stable ids), `config.personas(vertical)`; prompts + first_message
  use `{{company_name}}` so any company name can wear a persona. Provision reads
  persona `first_message` from yaml.
- **Synthetic market from real places** (Andrea's decision: NEVER call real
  businesses): `POST /api/jobs/{id}/companies/from-call-list` promotes discovered
  Google/OSM leads to companies with `source="synthetic"` + personas round-robin —
  real name on the board, simulated behavior. Discovery router now saves partial
  scans too (Google+OSM without Yelp is a valid list).
- **Real outbound call for the live demo** (Andrea on the phone as the vendor):
  `POST /api/jobs/{id}/calls/real {to_number, phase}` via ElevenLabs native Twilio
  outbound. NEEDS `ELEVENLABS_PHONE_NUMBER_ID` in `.env` (import a Twilio number in
  ElevenLabs dashboard → Phone Numbers). Untested until a number exists.
- Offline test: `python -m tests.callqueue_test` (market seeding, guards, status
  derivation).
- Tunnel churn hurts: every cloudflared restart = new URL → update `.env` +
  `python -m agents.provision` + API_BASE in Lovable. Current tunnel:
  https://partner-may-cheers-switched.trycloudflare.com

NOT yet done (needs Andrea's API keys / mic — in order):
1. Fill `.env` (copy `.env.example`): ELEVENLABS_API_KEY, OPENAI_API_KEY,
   GOOGLE_PLACES_API_KEY and YELP_API_KEY (OSM needs no key).
2. `uvicorn negotiator.server:app --port 8000` + `ngrok http 8000` → set PUBLIC_BASE_URL
   in `.env` → `python -m agents.provision`. **Provisioning has never run live** — if
   ElevenLabs rejects payloads, fix `_tool_body`/`_agent_body` in `agents/provision.py`
   (endpoints: POST /v1/convai/tools, /v1/convai/agents/create, PATCH to update).
3. **First live bridge call is the top risk** — audio pacing vs real agents is the one
   thing offline tests can't prove. Test: seed with `--with-sample-spec`, then
   `python -m simulation.run_calls --job job_X --phase quote --company co_X --listen`.
4. Voice modes need `brew install portaudio && pip install pyaudio`.
5. First live `packgen` run (e.g. `--vertical hvac --area 28203`) — the "AI writes the
   config sheet" demo beat; validate output loads before showing it.
6. `docs/LOVABLE_PROMPT.md` is rewritten for the domain-sheet backend (jobs list,
   schema-driven intake form + learned questions, domains page with AI generation)
   and has the live tunnel URL baked in — update API_BASE in Lovable if the tunnel
   restarts. `docs/DEMO_SCRIPT.md` is still moving-era: refresh before rehearsing.

## Users & auth

`negotiator/auth.py`: PBKDF2-salted passwords, opaque bearer tokens in SQLite
(`users`/`sessions` tables). Every `/api` job route requires `Authorization:
Bearer <token>` and is owner-scoped (someone else's job = 404); public: auth
endpoints, `/api/verticals`, `/api/intake-form`. `/agent-tools/*` stays open —
ElevenLabs calls it, not users. `POST /api/auth/register|login` → `{token,user}`;
`GET /api/me` → profile + own jobs. Demo account (seed creates it, owns all
pre-auth jobs): **demo@negotiator.app / demo1234**. Offline test:
`python -m tests.auth_test`.

## Commands

```bash
source .venv/bin/activate                                  # venv exists, deps installed
python -m tests.smoke_test                                 # offline e2e check (moving)
python -m tests.estimator_test                             # offline estimator/domain-sheet check (plumbing)
python -m tests.market_discovery_test                      # offline call-list service + frontend API
uvicorn negotiator.server:app --port 8000                  # API + agent-tool webhooks
python -m agents.provision                                 # upsert agents/tools (re-run after prompt/URL changes)
python -m negotiator.packgen --vertical hvac --area 28203  # AI-write a new domain sheet (needs OPENAI_API_KEY)
curl "localhost:8000/api/intake-form?vertical=plumbing"    # form = base + learned questions
python -m negotiator.seed --with-sample-spec               # demo job (confirmed) + 3 companies
python -m simulation.run_intake --job job_X                # voice interview (mic)
python -m simulation.run_calls --job job_X --phase quote|negotiate [--listen|--human|--parallel|--company co_X]
curl localhost:8000/api/jobs/job_X/report | python -m json.tool
```

## Gotchas

- Re-provision after ANY change to prompts, personas, vertical yaml, or PUBLIC_BASE_URL
  (registry.json makes it a PATCH, ~10s).
- `save_job_spec` intentionally resets `confirmed=false` — every spec change forces
  re-confirmation before calls (this is a demo talking point, don't "fix" it).
- Dynamic vars (job_id/company_id) reach tools by the LLM copying them from the prompt;
  if a persona misroutes ids, tighten the tool param descriptions in provision.py.
- Counterparty agents get NO logging tools by design; only caller/closer log.
- Numbers sanity anchor (moving): sample job benchmark = fair $1,315–$2,940, median $1,935
  (inside the brief's documented real spread $1,158–$6,506). Plumbing test job
  (water heater, within-24h, 60-y-o house, tight access): fair $1,792–$4,257, median $2,688.
- Learned questions live in `data/negotiator.db` (gitignored) keyed by (vertical, area_code):
  they persist across jobs but NOT across a db wipe — re-demo the learning beat after wipes.
- A job in an area with no dedicated sheet falls back to the domain's base sheet but keeps
  its own learned-question pool — new areas work day one, sharpen over time.
- Repo: https://github.com/andimatteo/conversational-agent (origin).
