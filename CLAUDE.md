# CLAUDE.md — session handoff for The Negotiator

Claude Code loads this file automatically at the start of every session. It is the
single source of truth for project state; update it when state changes.

## What this is

Hack-Nation 6th Global AI Hackathon, **ElevenLabs "The Negotiator" challenge**:
voice agents that phone a market, gather comparable itemised quotes, and negotiate.
Full brief context and judging criteria: see `docs/DEMO_SCRIPT.md` (each demo beat
maps to a success criterion).

Decisions (made 2026-07-18, do not re-litigate):
- **Vertical: moving** (Rock Hill → Charlotte demo scenario from the brief itself).
- **Counterparties: agent-to-agent** (3 personas with hidden pricing policies)
  **+ human-in-the-loop mode** (`--human`, user answers via mic). Twilio = stretch only.
- **Stack: Python/FastAPI backend + Lovable frontend** (prompt in `docs/LOVABLE_PROMPT.md`).
- Credits available: ElevenLabs, OpenAI (vision doc parsing), Tavily (call-list discovery).

## Architecture in one paragraph

`verticals/moving.yaml` is the vertical pack — spec schema, estimator questions,
benchmark price model, red-flag rules, fee taxonomy, negotiation levers, honesty
policy. `agents/prompts.py` GENERATES all system prompts from it (estimator /
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
- Full backend loop passes `.venv/bin/python -m tests.smoke_test` (no API keys needed):
  spec-confirmation guard (409 before confirm), red-flag engine (lowballer trips all 5),
  leverage gate (excludes company being called), report ranking (negotiated binding
  price-match wins, flagged-cheapest ranks last, $876 negotiation delta).
- All modules import against elevenlabs SDK **2.58.0**; `AudioInterface` contract
  (start/stop/output/interrupt) and `ConversationInitiationData(dynamic_variables=...)`
  confirmed against installed SDK. Prompts render `{{job_id}}` etc. correctly.

NOT yet done (needs Andrea's API keys / mic — in order):
1. Fill `.env` (copy `.env.example`): ELEVENLABS_API_KEY, OPENAI_API_KEY, TAVILY_API_KEY.
2. `uvicorn negotiator.server:app --port 8000` + `ngrok http 8000` → set PUBLIC_BASE_URL
   in `.env` → `python -m agents.provision`. **Provisioning has never run live** — if
   ElevenLabs rejects payloads, fix `_tool_body`/`_agent_body` in `agents/provision.py`
   (endpoints: POST /v1/convai/tools, /v1/convai/agents/create, PATCH to update).
3. **First live bridge call is the top risk** — audio pacing vs real agents is the one
   thing offline tests can't prove. Test: seed with `--with-sample-spec`, then
   `python -m simulation.run_calls --job job_X --phase quote --company co_X --listen`.
4. Voice modes need `brew install portaudio && pip install pyaudio`.
5. Paste `docs/LOVABLE_PROMPT.md` into Lovable, set API_BASE to the ngrok URL.
6. Rehearse `docs/DEMO_SCRIPT.md` (~6 min run-of-show).

## Commands

```bash
source .venv/bin/activate                                  # venv exists, deps installed
python -m tests.smoke_test                                 # offline e2e check
uvicorn negotiator.server:app --port 8000                  # API + agent-tool webhooks
python -m agents.provision                                 # upsert agents/tools (re-run after prompt/URL changes)
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
- Numbers sanity anchor: sample job benchmark = fair $1,315–$2,940, median $1,935
  (inside the brief's documented real spread $1,158–$6,506).
- Repo: https://github.com/andimatteo/conversational-agent (origin).
