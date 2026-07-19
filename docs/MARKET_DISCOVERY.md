# State-wide call-list discovery

The `market_discovery` package builds the real-world list of businesses the Caller
can phone. It is independent from the simulated market and queries:

- Google Places API (New), when `GOOGLE_PLACES_API_KEY` is configured;
- Yelp Fusion, when `YELP_API_KEY` is configured;
- OpenStreetMap through Overpass, without an API key.

Google and Yelp searches are distributed over an overlapping geographic grid built
from the full state bounding box. OSM uses the state's administrative boundary.
Results are restricted to the requested state, US phone numbers are normalized to
E.164, and records sharing a phone number are merged while preserving their sources.

No business is added to `companies` during discovery. The call list is cached on the
job, so uncalled businesses do not appear as declines in quote reports.

## Configuration

```dotenv
GOOGLE_PLACES_API_KEY=...
YELP_API_KEY=...
OVERPASS_URL=https://overpass-api.de/api/interpreter
```

Upstream APIs impose coverage, quota and rate limits, so “state-wide” describes the
search area, not a guarantee that every registered business will be returned. A
provider can fail or be skipped without losing results from the others.

## Frontend API

All endpoints require the existing Bearer token and enforce job ownership.

Generate and cache a call list:

```http
POST /api/jobs/{job_id}/call-list/discover
Authorization: Bearer <token>
Content-Type: application/json

{
  "state": "North Carolina",
  "query": "plumbing company",
  "target_per_provider": 250
}
```

`query` may be empty; the backend then uses `meta.counterparty_noun` from the job's
domain sheet. `target_per_provider` is distributed across the state grid and is a
target rather than a hard cap when the grid contains more cells.

Read the last successfully generated list without calling upstream APIs:

```http
GET /api/jobs/{job_id}/call-list
Authorization: Bearer <token>
```

The sources are fixed: every generation attempts Google Places, Yelp Fusion and
OpenStreetMap. They cannot be deselected by the client. Both calls return
`generated_at`, `query`, `state`, `target_per_provider`, `required_sources`,
`complete`, per-source `provider_status`, `raw_results`, `total`, and `items`.
`complete` is true only when all three searches succeeded; missing API keys and
upstream errors remain visible rather than silently producing an apparently complete
list. Partial POST results are returned for diagnostics but are not cached as the
job's final call list (`saved=false`); only `complete=true` is persisted. Every item
has a normalized
`phone`, combined `sources`, provider `source_ids`, location, rating/reviews and URL
when supplied upstream.

Run the offline service and endpoint test with:

```bash
python -m tests.market_discovery_test
```
