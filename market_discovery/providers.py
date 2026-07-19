"""Adapters for the three business-data sources.

Each adapter returns the same internal model. Provider failures are isolated by
the service layer so one unavailable API never destroys the whole call list.
"""
import math
import os
import re

import httpx

from .geo import grid
from .models import Business, StateArea


def _state_from_address(address: str) -> str:
    match = re.search(r",\s*([A-Z]{2})(?:\s+\d{5}(?:-\d{4})?)?\s*(?:,|$)", address or "")
    return match.group(1) if match else ""


class GooglePlacesProvider:
    name = "google_places"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key if api_key is not None else os.getenv("GOOGLE_PLACES_API_KEY", "")

    def enabled(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, area: StateArea, target: int) -> list[Business]:
        points = grid(area, spacing_km=65)  # 50 km radius, overlapping even diagonally
        per_cell = max(1, math.ceil(target / len(points)))
        headers = {
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": (
                "places.id,places.displayName,places.nationalPhoneNumber,"
                "places.internationalPhoneNumber,places.formattedAddress,places.location,"
                "places.rating,places.userRatingCount,places.googleMapsUri,places.types,"
                "nextPageToken"
            ),
        }
        found: list[Business] = []
        for latitude, longitude in points:
            page_token, collected = "", 0
            while collected < per_cell:
                body = {
                    "textQuery": query,
                    "pageSize": min(20, per_cell - collected),
                    "regionCode": "US",
                    "includePureServiceAreaBusinesses": True,
                    "locationBias": {"circle": {
                        "center": {"latitude": latitude, "longitude": longitude},
                        "radius": 50000.0,
                    }},
                }
                if page_token:
                    body["pageToken"] = page_token
                response = httpx.post(
                    "https://places.googleapis.com/v1/places:searchText",
                    headers=headers, json=body, timeout=30,
                )
                response.raise_for_status()
                payload = response.json()
                places = payload.get("places", [])
                for place in places:
                    location = place.get("location", {})
                    address = place.get("formattedAddress", "")
                    found.append(Business(
                        name=place.get("displayName", {}).get("text", ""),
                        phone=(place.get("internationalPhoneNumber")
                               or place.get("nationalPhoneNumber", "")),
                        source=self.name,
                        source_id=place.get("id", ""),
                        address=address,
                        state=_state_from_address(address),
                        latitude=location.get("latitude"),
                        longitude=location.get("longitude"),
                        rating=place.get("rating"),
                        review_count=place.get("userRatingCount"),
                        url=place.get("googleMapsUri", ""),
                        categories=place.get("types", []),
                    ))
                collected += len(places)
                page_token = payload.get("nextPageToken", "")
                if not places or not page_token:
                    break
        return found


class YelpProvider:
    name = "yelp"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key if api_key is not None else os.getenv("YELP_API_KEY", "")

    def enabled(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, area: StateArea, target: int) -> list[Business]:
        points = grid(area, spacing_km=50)  # Yelp radius is capped at 40 km
        per_cell = max(1, math.ceil(target / len(points)))
        headers = {"Authorization": f"Bearer {self.api_key}"}
        found: list[Business] = []
        for latitude, longitude in points:
            offset, collected = 0, 0
            while collected < per_cell and offset < 1000:
                page_size = min(50, per_cell - collected)
                response = httpx.get(
                    "https://api.yelp.com/v3/businesses/search",
                    headers=headers,
                    params={"term": query, "latitude": latitude, "longitude": longitude,
                            "radius": 40000, "limit": page_size, "offset": offset,
                            "sort_by": "best_match"},
                    timeout=30,
                )
                response.raise_for_status()
                businesses = response.json().get("businesses", [])
                for business in businesses:
                    location = business.get("location", {})
                    coordinates = business.get("coordinates", {})
                    found.append(Business(
                        name=business.get("name", ""),
                        phone=business.get("phone", ""),
                        source=self.name,
                        source_id=business.get("id", ""),
                        address=", ".join(location.get("display_address", [])),
                        city=location.get("city", ""),
                        state=location.get("state", ""),
                        latitude=coordinates.get("latitude"),
                        longitude=coordinates.get("longitude"),
                        rating=business.get("rating"),
                        review_count=business.get("review_count"),
                        url=business.get("url", ""),
                        categories=[category.get("title", "")
                                    for category in business.get("categories", [])],
                    ))
                collected += len(businesses)
                if len(businesses) < page_size:
                    break
                offset += page_size
        return found


class OpenStreetMapProvider:
    name = "openstreetmap"

    def enabled(self) -> bool:
        return True

    def search(self, query: str, area: StateArea, target: int) -> list[Business]:
        terms = [re.escape(word) for word in re.findall(r"[a-z0-9]+", query.casefold())
                 if len(word) > 2 and word != "company"]
        pattern = "|".join(terms) or re.escape(query.casefold())
        overpass_query = f'''[out:json][timeout:120];
area["ISO3166-2"="US-{area.code}"][admin_level=4]->.state;
(
  nwr(area.state)["phone"]["name"~"{pattern}",i];
  nwr(area.state)["contact:phone"]["name"~"{pattern}",i];
  nwr(area.state)["craft"~"{pattern}",i]["phone"];
  nwr(area.state)["craft"~"{pattern}",i]["contact:phone"];
  nwr(area.state)["office"~"{pattern}",i]["phone"];
  nwr(area.state)["office"~"{pattern}",i]["contact:phone"];
  nwr(area.state)["shop"~"{pattern}",i]["phone"];
  nwr(area.state)["shop"~"{pattern}",i]["contact:phone"];
);
out tags center {target};'''
        response = httpx.post(
            os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter"),
            content=overpass_query,
            headers={"User-Agent": "QuoteWise/1.0 (call-list discovery)"},
            timeout=150,
        )
        response.raise_for_status()
        found: list[Business] = []
        for element in response.json().get("elements", [])[:target]:
            tags = element.get("tags", {})
            position = element.get("center", element)
            found.append(Business(
                name=tags.get("name", ""),
                phone=tags.get("contact:phone") or tags.get("phone", ""),
                source=self.name,
                source_id=f'{element.get("type", "")}/{element.get("id", "")}',
                address=" ".join(filter(None, [tags.get("addr:housenumber"),
                                                 tags.get("addr:street")])),
                city=tags.get("addr:city", ""),
                state=tags.get("addr:state", area.code),
                latitude=position.get("lat"),
                longitude=position.get("lon"),
                url=tags.get("website") or tags.get("contact:website", ""),
                categories=[value for key, value in tags.items()
                            if key in ("craft", "office", "shop")],
            ))
        return found


def default_providers():
    return [GooglePlacesProvider(), YelpProvider(), OpenStreetMapProvider()]
