# Demo run-of-show (~6 minutes)

Every beat below maps to a judging criterion from the brief. Play audio out loud —
the calls ARE the product.

## 0. The hook (30s)
"Real quotes for the same 45-mile move: $1,158 to $6,506. The fair price is in there —
extracting it costs eight phone calls nobody makes. We built the thing that makes them."

## 1. Intake — two doors, one spec (60s)
- Play a 30–40s clip of the **voice interview** (Estimator asking about stairs, parking
  distance — the "long carry" fee trap a consumer never thinks to mention).
- Upload `samples/existing_quote.txt` (an old written quote) → show it parses into the
  **same JSON schema**, and its $1,574 binding total becomes *leverage in the DB*.
- Click **Confirm spec**. Point at the guard: the `get_job_spec` webhook returns 409
  until confirmed — *calls are impossible before the user signs off*.
  ✅ *criterion: one spec, voice + document, confirmed, reused verbatim*

## 2. Quote calls — three negotiation styles (2min)
Run `--phase quote`, play highlights:
- **Summit & Sons (stonewaller):** "We don't quote over the phone." The Caller's complete
  spec unlocks a number anyway. Interruptions + barge-in are real (audio bridge is paced
  PCM, not turn-based text).
- **QuickBudget (lowballer):** quotes ~$1,050 sight-unseen. The Caller checks the
  benchmark tool, flags 30%-below-market *on the call*, and interrogates fee by fee:
  stairs +$105, fuel +$84, long carry +$95... The "cheap" quote inflates on record.
- **Premier Coast (upseller):** $2,900 with auto-bundled packing; "price valid today."
  The Caller strips unspec'd add-ons and logs the pressure tactic as a condition.
- Show the dashboard: three **itemised, comparable** quotes, red flags computed.
  ✅ *criterion: 3 distinct styles, structured itemised capture*
- "Am I talking to a robot?" moment: agent answers honestly, keeps the quote.
  ✅ *criterion: AI disclosure handled gracefully*

## 3. The negotiation — price moves for a reason (90s)
Run `--phase negotiate` against Premier Coast. The Closer cites the REAL Summit quote
by name and number (source: `get_competing_quotes` — it has no other way to make that
claim), asks for the match. Persona policy grants price-match −5% *only because the
trigger is genuinely met*. Play the concession sentence.
✅ *criterion: price measurably moves mid-call from gathered leverage, not script*
Then the honesty line: "The agent cannot bluff — the leverage tool reads the quote DB.
No quote in the DB, no claim on the call."

## 4. Human on the line (45s — live if the room allows)
`--phase quote --human`: a judge (or you) answers the phone as a mover, ad-libs
friction. Nothing is scripted on either side.

## 5. The report (45s)
Open the ranked report: recommendation in plain language, initial → negotiated deltas,
red-flag explanations ("QuickBudget was cheapest and is ranked last — here's why"),
every claim linked to a conversation transcript + recording.
✅ *criteria: ranked report, transcript evidence, structured outcome on every call*

## 6. The kicker (15s)
Open `verticals/moving.yaml`: "Auto body shops, freight, wedding vendors — this file
is the only thing that changes." (Show the Providers page: the real state-wide call
list merged from Google Places, Yelp and OSM — where the market comes from outside the demo.)

## Failure modes to rehearse
- Counterparty hangs up → outcome=hangup logged, report shows a documented decline. Fine.
- Agent misses a fee → it's visible in the itemisation gap; say so honestly, it proves
  the eval story.
- ngrok died → re-run provision with the new URL (30s).
