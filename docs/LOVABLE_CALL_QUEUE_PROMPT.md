# Lovable prompt — live call queue + real-call demo

Paste the block below into the existing Lovable project as one message.
(Also update API_BASE: https://partner-may-cheers-switched.trycloudflare.com)

---

Rework the Calls page (`/job/:jobId/calls`) into a **live call queue** and wire
the call-list page into it. Keep everything not mentioned as it is.

**1. Build the market (on the Call list page `/job/:jobId/call-list`)**
- Under the discovered-places table add a primary button **"Use top 3 as demo
  market"** → `POST /api/jobs/:jobId/companies/from-call-list {count: 3}`.
  Show the response `note` as an info banner: real business names, behavior
  SIMULATED by counterparty agents — no real business is ever called.
- Alternative button "Use simulated companies instead" →
  `POST /api/jobs/:jobId/companies/simulated` (3 fictional companies, one per
  negotiation style).

**2. The live queue (`/job/:jobId/calls`) — poll `GET /api/jobs/:jobId/call-queue`
every 3s** → `{running, queue: [{company:{id,name,persona,source}, status,
last_call_kind, conversation_id, initial_total, negotiated_total, red_flags}]}`.
- One row per company: name, persona style tag, a "synthetic" chip when
  `source == "synthetic"`, live **status pill**: `to_call` gray outline,
  `queued` gray, `calling` indigo with pulsing dot, `quote` green,
  `callback` amber, `decline`/`hangup` dark gray. Totals: `initial_total`,
  and `negotiated_total` in bold green when present with the initial struck
  through; red-flag count badge.
- **Start quote calls** button (disabled while `running`) →
  `POST /api/jobs/:jobId/calls/start {phase: "quote", parallel: false}`.
  **Start negotiations** button → same with `phase: "negotiate"` (enabled only
  when at least one quote exists). 409 → show the server message (spec not
  confirmed / already running).
- While `running`: header banner "📞 Calls in progress — the queue resolves
  live" with an animated indicator.
- Keep the transcript slide-over from `/calls` + `/quotes` data as today.

**3. Live phone demo card** (bottom of the queue page):
"📱 **Live negotiation on a real phone** — our agent calls a human vendor."
Inputs: phone number (E.164 placeholder +1…), company label (default "Live
Vendor (phone)"), phase select (quote/negotiate) → **Call now** →
`POST /api/jobs/:jobId/calls/real {to_number, phase, company_name}`.
The new company row appears in the queue and resolves like any other.
On 503 show the server's message (phone number not configured); on 502 show
the ElevenLabs error.
