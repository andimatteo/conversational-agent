from datetime import datetime, timezone

from .geo import NominatimGeocoder
from .normalize import merge_businesses
from .providers import GooglePlacesProvider, default_providers

REQUIRED_SOURCES = ("google_places", "yelp", "openstreetmap")


class DiscoveryService:
    def __init__(self, providers=None, geocoder=None):
        self.providers = providers if providers is not None else default_providers()
        self.geocoder = geocoder or NominatimGeocoder()

    def discover(self, query: str, state: str, target_per_provider: int = 250) -> dict:
        query, state = query.strip(), state.strip()
        if not query or not state:
            raise ValueError("query and state are required")
        area = self.geocoder.resolve_state(state)
        known = {provider.name for provider in self.providers}
        missing_adapters = set(REQUIRED_SOURCES) - known
        if missing_adapters:
            raise ValueError(f"missing required provider adapters: {', '.join(sorted(missing_adapters))}")

        raw_rows, statuses = [], {}
        for provider in self.providers:
            if provider.name not in REQUIRED_SOURCES:
                continue
            if not provider.enabled():
                statuses[provider.name] = {"status": "skipped", "reason": "API key missing"}
                continue
            try:
                rows = provider.search(query, area, target_per_provider)
                raw_rows.extend(rows)
                statuses[provider.name] = {"status": "ok", "results": len(rows)}
            except Exception as exc:
                statuses[provider.name] = {"status": "error", "reason": str(exc)[:300]}

        allowed_states = {area.code.casefold(), area.name.casefold()}
        callable_rows = [row for row in merge_businesses(raw_rows)
                         if not row.state or row.state.casefold() in allowed_states]
        callable_rows.sort(key=lambda row: (
            -(row.rating or 0), -(row.review_count or 0), row.name.casefold()
        ))
        complete = all(statuses.get(source, {}).get("status") == "ok"
                       for source in REQUIRED_SOURCES)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "query": query,
            "state": {"name": area.name, "code": area.code},
            "target_per_provider": target_per_provider,
            "required_sources": list(REQUIRED_SOURCES),
            "complete": complete,
            "provider_status": statuses,
            "raw_results": len(raw_rows),
            "total": len(callable_rows),
            "items": [row.to_dict() for row in callable_rows],
        }

    def discover_google_places(self, query: str, state: str, target: int = 25) -> dict:
        """Perform a fresh Google Places Text Search for the launch workflow.

        This is deliberately separate from transcript simulation. A successful
        result proves that the displayed company identities came from a live
        Places API request made after the user reviewed the job.
        """
        query, state = query.strip(), state.strip()
        if not query or not state:
            raise ValueError("query and state are required")
        area = self.geocoder.resolve_state(state)
        provider = next(
            (row for row in self.providers if row.name == "google_places"),
            GooglePlacesProvider(),
        )
        if not provider.enabled():
            raise ValueError("GOOGLE_PLACES_API_KEY is required to launch this job")
        rows = provider.search(query, area, target)
        allowed_states = {area.code.casefold(), area.name.casefold()}
        callable_rows = [
            row for row in merge_businesses(rows)
            if row.phone and (not row.state or row.state.casefold() in allowed_states)
        ]
        callable_rows.sort(key=lambda row: (
            -(row.rating or 0), -(row.review_count or 0), row.name.casefold()
        ))
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "query": query,
            "state": {"name": area.name, "code": area.code},
            "target_per_provider": target,
            "required_sources": ["google_places"],
            "complete": True,
            "saved": bool(callable_rows),
            "provider_status": {
                "google_places": {"status": "ok", "results": len(rows), "live_api": True}
            },
            "raw_results": len(rows),
            "total": len(callable_rows),
            "items": [row.to_dict() for row in callable_rows],
            "discovery_mode": "live_google_places_at_launch",
        }
