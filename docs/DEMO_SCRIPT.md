# Demo run-of-show: document intake → first-batch explorer → automatic closer

This is one resettable hybrid job with one consenting human role-player and two
real phone calls total.

- A fresh Google Places API request after the final review supplies every displayed
  market identity and coordinate.
- One resulting Google identity is represented by the consenting human at the
  server allow-listed demo destination. Its stored Google phone and place ID are
  never changed or sent to the provider.
- The imported Twilio source number ends in `5722`. It is the outbound
  caller ID, not the destination.
- The selected human belongs to **quote batch 1**. That first call explores the
  scope and gathers an itemised quote; it does not negotiate.
- All other vendors produce visibly labelled synthetic demo-market chats. Their
  transcript turns and final quote use the same persistent Call ID.
- After every quote batch crosses its hard barrier, the backend automatically
  calls the same human once more with the final grounded knowledge snapshot.
- The operator's one Review/Launch click explicitly authorizes those two calls. There is
  no second frontend POST and no call loop.

Synthetic offers may be used only as explicitly disclosed **simulated
demo-market offers**. Never say the named Google businesses were contacted or
made those offers.

## What the demo proves

It proves the connected loop:

1. document intake;
2. short browser voice completion;
3. user review, live Google Places discovery and automatic launch;
4. all-vendor square-root quote batching with progressive knowledge;
5. one live exploratory quote from a consenting human;
6. one live grounded negotiation after all quote barriers;
7. real-time dashboard updates and an evidence-backed comparison.

The synthetic rows prove orchestration, conversation design and structured
evidence—not real calls to those Google businesses. Do not describe this one
run as three live counterpart styles.

## Preflight—no calls

1. Keep `DEBUG_CALLS=true`. The prepared demo is a narrow exception that routes
   only its post-discovery selected identity to `DEMO_PHONE_NUMBER`.
2. Confirm `DEMO_PHONE_NUMBER` is the consenting human, while the ElevenLabs
   imported Twilio number ending `5722` is selected by
   `ELEVENLABS_PHONE_NUMBER_ID`.
3. Confirm `ELEVENLABS_API_KEY`, `AGENT_TOOL_SECRET`, `PUBLIC_BASE_URL`, and the
   public webhook route.
4. Restart FastAPI after environment changes and run
   `python -m agents.provision` after agent/tool/tunnel changes.
5. Confirm authenticated `GET /api/runtime-config` returns:
   `debug_mode=true`, `demo_phone_configured=true`,
   `twilio_number_configured=true`, and
   `demo_intake_pdf_url="/api/demo/intake-pdf"`.
6. Confirm the Lovable API base is the current tunnel:
   `https://travesti-championship-presented-machinery.trycloudflare.com`.
7. Confirm `GOOGLE_PLACES_API_KEY` is configured; discovery must not fall back to cache.
8. Ensure no prior demo has active or leased work.

None of these checks starts a conversation or phone call.

## 0. Prepare a fresh unconfirmed job—no calls

Run:

```bash
python -m negotiator.demo_reset
```

To request a specific identity from the later live result:

```bash
python -m negotiator.demo_reset --live-vendor "Exact Google vendor name"
```

The command creates a new **unconfirmed** plumbing job with an empty spec, enables
automatic negotiation, and archives old demo jobs without deleting their evidence.
It performs no discovery, promotes no company and starts no call. Discovery is always
fresh and deferred until the reviewed `/launch` action. `--rediscover` is deprecated.
Do not use `--wipe-learnings` during a normal run.

Refresh Lovable, open the returned `job_id`, and show that it is awaiting intake
and confirmation.

## 1. Download and upload the scope PDF—30 seconds

On Intake click **Download the demo intake PDF**. Lovable fetches authenticated:

```http
GET /api/demo/intake-pdf
```

It downloads `QuoteWise-water-heater-intake.pdf`, served from the reproducible
repository asset `assets/demo/water_heater_intake.pdf`.

Drag that downloaded file into the normal document dropzone. This sends:

```http
POST /api/jobs/{job_id}/documents
Content-Type: multipart/form-data
```

Show the parser filling the common structured spec. The document describes a
40-gallon natural-gas water-heater replacement in a ground-floor Charlotte
garage and intentionally leaves three quick confirmations:

- urgency: this week or flexible;
- whether the main water shutoff is known and operational;
- a normal-hours weekday access window.

The PDF is customer scope, not a vendor quote.

## 2. Complete the short browser voice intake—under one minute

Click **Start voice intake**. Lovable requests:

```http
POST /api/jobs/{job_id}/voice-session
```

Answer only the missing confirmations, for example:

- service is needed this week;
- the shutoff is known, accessible and operational;
- a normal-hours weekday appointment works.

The Estimator must acknowledge the document, avoid re-asking saved facts, merge
only new answers, and finish. Show its browser transcript as it happens. When it
disconnects, Lovable refetches the Job and form.

## 3. Review, discover and launch—one deliberate click

Open Spec and show the one structured scope built by document + voice. Point out
that every vendor receives this exact confirmed scope.

Read the consent and check it:

> I reviewed the job and authorize exactly two live calls to the configured human
> role-player. No Google business will be called.

Submit one fresh idempotency key:

```http
POST /api/jobs/{job_id}/launch
Content-Type: application/json

{
  "idempotency_key": "<fresh UUID>",
  "authorize_demo_calls": true
}
```

This one endpoint validates the spec, calls Google Places live, saves and promotes
every callable result, selects the role-play identity, confirms the job and starts
the batches. Do not send state, query, `company_ids`, `parallel`, a phone number or a
negotiation request. The Call List page does not exist. On success Lovable navigates
directly to Calls and shows the live Places discovery receipt.

The response exposes:

- `total`: N quote vendors;
- `total_calls`: N+1 including the callback;
- `batch_size`: `ceil(sqrt(N))`;
- `quote_batch_count`;
- `batch_count`: quote batches plus the callback;
- `auto_negotiation_batch`: the final batch index;
- `auto_negotiation_status=waiting_for_quote_batches`;
- `demo_calls_authorized=true`.

## 4. First batch: live exploratory quote + streaming market

Answer when the selected target enters `calling` in **batch 1**. The Caller must:

1. disclose it is an AI calling for a customer;
2. disclose the recorded human role-play and that you do not represent the real
   Google-listed business;
3. describe the confirmed job consistently;
4. ask exploratory questions about scope assumptions, inclusions and exclusions;
5. request labour, equipment, materials, permit, disposal and warranty as
   separate line items;
6. confirm all-in total, binding status, deposit and validity;
7. avoid every concession, price-match or competing-offer question.

Give a plausible, itemised initial offer, but do not make it the final winner yet.
The first-batch frozen snapshot contains no later peer knowledge.

While speaking, show the other batch rows. Synthetic Call records update one turn
at a time with:

- `transcript_streaming=true`;
- increasing `transcript_turn_count`;
- `last_transcript_at` and the latest turn preview.

The Quote appears only after that same Call becomes terminal and references its
exact `call_id`. The UI never writes a provisional quote.

At the top, show backend-authoritative current best, observed range, and
called/total. Show batch progress and knowledge version. The next quote batch
cannot begin until every first-batch call—including the live human—terminates.

## 5. Hard quote barriers and progressive knowledge

As each later synthetic batch runs, open two transcript drawers and show distinct
vendor behavior. Say explicitly:

“These are generated demo conversations attached to real Google market identities.
Those businesses were not contacted, and no audio exists.”

At each complete barrier:

- the batch becomes terminal;
- structured quotes become visible;
- best/range/called update from server evidence;
- learned intake questions persist with call/company provenance;
- the knowledge version advances once;
- only then does the next batch start.

Do not claim same-batch information was available early.

## 6. Automatic live negotiation callback—normally within one minute

After the final quote barrier, do **not** click or POST anything. The same durable
run creates the callback as `auto_negotiation_batch`, with:

- `phase=negotiate`;
- `auto_negotiation=true`;
- the same selected `company_id`;
- `mode=demo_phone`;
- `dialed_to=configured_demo_phone`;
- `recall_slot=1`.

Lovable shows `waiting_for_quote_batches → running → completed/failed` and an
observational “expected within one minute” timer. If delayed, it keeps polling and
never redials.

Answer the second call. The Closer must repeat the AI and role-play disclosure,
then negotiate using exact grounded database claims. Any synthetic claim must be
phrased in the same sentence as:

> a simulated demo-market offer labelled [company] at $X

It must not say that the named Google business quoted or promised that amount.
Respond with a lower itemised all-in offer after the agent makes a sensible,
grounded ask. Restate the final total, binding status, deposit, validity and any
waived fee clearly.

## 7. Show the verified result and map—45 seconds

After the call terminalizes:

- show initial → negotiated total and savings on the same vendor row;
- open the exact concession and leverage IDs in the transcript;
- play authenticated audio for the two live role-play attempts;
- show the sticky best/range/called update;
- open Compare and show ranking, itemisation, red flags and evidence;
- show the Places map: one price pin per geolocated offer and a starred pin for
  the backend-selected preferred result.

Celebrate **New best offer, verified in the live negotiation** only if the server's
`summary.current_best_offer.company_id` is the selected role-player and the
negotiated quote is evidence-, grounding- and itemisation-verified. Otherwise show
the honest actual winner.

State plainly: only the allow-listed consenting human was dialled; no Google
business received a call.

## Failure modes to rehearse

- **PDF unavailable:** verify authenticated `GET /api/demo/intake-pdf`; do not
  fabricate form values.
- **Parsing fails:** show the server detail and retry the same file deliberately.
- **Voice unavailable:** verify microphone permission, Estimator provisioning and
  tunnel. Do not mark intake complete.
- **Launch rejected:** complete intake and the explicit two-call checkbox; verify Places and both
  phone configuration flags. Do not bypass `authorize_demo_calls`.
- **Target does not ring:** verify the allow-listed destination, imported Twilio
  source, API key, tunnel and provisioned agents. Do not use a Google phone.
- **Initial live quote is unverified:** automatic negotiation stays blocked. Report
  it honestly and prepare a fresh demo only after active work is terminal.
- **Callback delayed:** keep polling; no client POST, no automatic retry.
- **Provider state uncertain:** automatic redial remains locked until reconciled.
- **No concession:** show the actual result; never invent movement.
- **Audio missing:** retain transcript/evidence but never claim recording proof.
- **Tunnel changes:** update the one Lovable `API_BASE`, restart FastAPI and
  re-provision agents.
