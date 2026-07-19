"""Deprecated compatibility module.

Call lists are intentionally built only by ``market_discovery`` from Google
Places, Yelp Fusion and OpenStreetMap. Use the authenticated frontend endpoints:

    POST /api/jobs/{job_id}/call-list/discover
    GET  /api/jobs/{job_id}/call-list
"""


def discover(*_args, **_kwargs):
    raise RuntimeError("Tavily discovery was retired; use the multi-provider call-list endpoint")
