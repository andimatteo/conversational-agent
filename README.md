# THE NEGOTIATOR

Voice agents that call, compare, and haggle — built for the Hack-Nation × ElevenLabs
challenge. Vertical: **household moving** (the market with the receipts: a documented
$1,158–$6,506 spread for the same 45-mile move, FMCSA's 40% sight-unseen overrun stat,
13k+ BBB complaints/yr).

## The loop

```
01 ESTIMATOR                02 CALLER                     03 CLOSER
voice interview  ─┐         calls each company,           calls back with leverage:
                  ├─► one   describes the SAME spec,  ─►  real competing bids, fee
document intake  ─┘  spec   extracts itemised quotes      challenges, price-match
(photo / old quote)  (user  (3 distinct personas)         └─► ranked report with
                   confirms)                                  transcript evidence
```

Everything vertical-specific — spec schema, estimator questions, price benchmarks,
red-flag rules, negotiation levers, fee taxonomy — lives in `verticals/moving.yaml`.
Switching to auto body shops = writing `verticals/auto_body.yaml`. No code changes.

**Honesty as architecture:** the Closer's only source of competitive claims is the
`get_competing_quotes` webhook, which reads the real quote DB. It structurally cannot
invent a bid. AI disclosure is in every opening line and the "am I talking to a robot?"
answer is scripted honest. Every call must end through `log_call_outcome`
(quote | callback | decline | hangup) — never "they said around two thousand."

**The market** is three ElevenLabs counter-agents with *hidden* pricing policies
(anchor, floor, concession rules, hidden fees) served by a private "back office" tool —
prices move mid-call only when the negotiator earns it. Plus a human-in-the-loop mode
where you answer the phone yourself.

| Persona | Company | Style |
|---|---|---|
| stonewaller | Summit & Sons Moving | gruff; "we don't quote over the phone"; fair floor |
| lowballer | QuickBudget Movers | 40%-below-market bait quote; fees revealed only under interrogation |
| upseller | Premier Coast Van Lines | 1.45x anchor, auto-bundled add-ons, "price valid today" |

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# for voice modes (intake / --listen / --human): brew install portaudio && pip install pyaudio
cp .env.example .env       # fill in ELEVENLABS_API_KEY, OPENAI_API_KEY, TAVILY_API_KEY
```

Webhook tools need a public URL for the API server:

```bash
uvicorn negotiator.server:app --port 8000          # terminal 1
ngrok http 8000                                    # terminal 2 → put https URL in .env as PUBLIC_BASE_URL
python -m agents.provision                         # creates/updates 6 agents + 7 tools on ElevenLabs
```

`agents/registry.json` remembers the ElevenLabs ids; re-running provision PATCHes in
place (do this after any prompt/config/URL change).

## Run the demo

```bash
python -m negotiator.seed --with-sample-spec       # Daniel's Rock Hill→Charlotte 2BR move + 3 companies
# — or the full intake path:
python -m negotiator.seed                          # empty job
python -m simulation.run_intake --job job_XXXX     # voice interview (mic)
curl -F "file=@samples/existing_quote.txt" localhost:8000/api/jobs/job_XXXX/documents
curl -X POST localhost:8000/api/jobs/job_XXXX/confirm    # user signs off — calls unlock

python -m simulation.run_calls --job job_XXXX --phase quote --listen     # 3 quote calls
python -m simulation.run_calls --job job_XXXX --phase negotiate --listen # closer calls back with leverage
python -m simulation.run_calls --job job_XXXX --phase quote --human      # YOU answer via mic

curl localhost:8000/api/jobs/job_XXXX/report | python -m json.tool       # ranked, evidence-backed
curl "localhost:8000/api/jobs/job_XXXX/market?city=Charlotte&state=NC"   # real-world call list (Tavily)
```

Recordings land in `data/recordings/` (per-side WAVs + the ElevenLabs conversation MP3);
transcripts are pulled from the ElevenLabs conversation API onto each call record.

## Repo map

```
verticals/moving.yaml     the vertical pack (THE config — schema, benchmarks, red flags, levers)
agents/personas.yaml      the simulated market: hidden pricing policies per persona
agents/prompts.py         all conversation design, generated from the vertical pack
agents/provision.py       creates agents + webhook tools on ElevenLabs (idempotent)
negotiator/server.py      FastAPI: product API + mid-call agent-tool webhooks
negotiator/benchmarks.py  price model, red-flag engine, counterparty ground truth
negotiator/report.py      ranking + plain-language recommendation with citations
negotiator/docparse.py    document intake (OpenAI vision) → same spec schema
negotiator/discovery.py   Tavily call-list discovery
simulation/bridge.py      agent↔agent real-time audio bridge (paced PCM, real barge-in)
simulation/run_calls.py   orchestrates quote/negotiate calls; human mode; parallel mode
docs/DEMO_SCRIPT.md       judge-facing run-of-show mapped to the success criteria
docs/LOVABLE_PROMPT.md    paste into Lovable to generate the dashboard (API contract included)
```

If an ElevenLabs API shape has drifted (they ship fast), the two touchpoints are
`agents/provision.py` (`/v1/convai/tools`, `/v1/convai/agents/create`) and the SDK's
`Conversation` class used in `simulation/`.
