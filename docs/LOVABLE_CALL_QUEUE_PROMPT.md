# Lovable prompt — superseded

Do not use this legacy queue-only prompt. Its manual callback flow has been
retired and could conflict with the backend-owned two-call campaign.

Use [`LOVABLE_DEMO_COMPLETE_PROMPT.md`](LOVABLE_DEMO_COMPLETE_PROMPT.md) as the
single authoritative Lovable prompt. It covers the complete application and
the current resettable demo contract:

- document plus short browser-voice intake;
- explicit user confirmation of the unified specification;
- one checkbox authorising exactly two live calls to the allow-listed human;
- exploratory human call in quote batch one;
- N−1 progressively persisted synthetic conversations;
- hard batch barriers and versioned knowledge snapshots;
- automatic grounded negotiation callback after every quote batch;
- realtime best offer, range, called/total, evidence, transcripts and audio.

The frontend must never submit a destination phone number, schedule the callback
itself, retry a live call automatically, or create GitHub workflows.
