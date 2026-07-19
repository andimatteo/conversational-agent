import math

import httpx

from .models import StateArea


class NominatimGeocoder:
    url = "https://nominatim.openstreetmap.org/search"

    def resolve_state(self, state: str) -> StateArea:
        r = httpx.get(
            self.url,
            params={"q": f"{state}, USA", "format": "jsonv2", "limit": 1,
                    "countrycodes": "us", "addressdetails": 1},
            headers={"User-Agent": "QuoteWise/1.0 (call-list discovery)"},
            timeout=25,
        )
        r.raise_for_status()
        rows = r.json()
        if not rows:
            raise ValueError(f"State not found: {state}")
        row = rows[0]
        south, north, west, east = map(float, row["boundingbox"])
        iso_code = row.get("address", {}).get("ISO3166-2-lvl4", "")
        code = iso_code.rsplit("-", 1)[-1].upper()
        if len(code) != 2:
            raise ValueError(f"Could not determine the two-letter code for state: {state}")
        name = row.get("address", {}).get("state") or state
        return StateArea(name, code, south, west, north, east)


def grid(area: StateArea, spacing_km: float) -> list[tuple[float, float]]:
    """Cover the full bounding box with overlapping provider search radii."""
    lat_step = spacing_km / 111.0
    lat_count = max(1, math.ceil((area.north - area.south) / lat_step))
    points = []
    for lat_index in range(lat_count + 1):
        lat = area.south + (area.north - area.south) * lat_index / lat_count
        lon_step = spacing_km / max(25.0, 111.0 * math.cos(math.radians(lat)))
        lon_count = max(1, math.ceil((area.east - area.west) / lon_step))
        for lon_index in range(lon_count + 1):
            lon = area.west + (area.east - area.west) * lon_index / lon_count
            points.append((lat, lon))
    return list(dict.fromkeys((round(lat, 5), round(lon, 5)) for lat, lon in points))
