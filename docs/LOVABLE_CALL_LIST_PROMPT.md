# Retired — do not build a Call List page

The Call List UI has been removed. Use
[`LOVABLE_DEMO_COMPLETE_PROMPT.md`](LOVABLE_DEMO_COMPLETE_PROMPT.md).

The final Spec review calls `POST /api/jobs/{job_id}/launch`; the backend performs
a fresh Google Places API request, promotes every callable result, selects the
role-play identity and starts the batches. Lovable navigates directly to Calls.
