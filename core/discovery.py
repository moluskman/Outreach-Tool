"""Find candidate businesses using the Google Places API (New)."""
from __future__ import annotations

import requests

from models import Lead

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.nationalPhoneNumber",
        "places.websiteUri",
        "places.rating",
        "places.userRatingCount",
        "places.types",
    ]
)


def find_businesses(
    query: str,
    location: str,
    api_key: str,
    radius_m: int = 5000,
    limit: int = 20,
) -> list[Lead]:
    """Search Places API for `query` near `location` and return them as Leads.

    `location` is passed as free text appended to the query (e.g. "hairdresser in
    Berwick, VIC") which Places' text search geocodes itself — no separate
    geocoding call needed. `radius_m` is used as a soft bias, not a hard filter,
    since text search doesn't support a strict radius cutoff.
    """
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": FIELD_MASK,
    }
    body = {
        "textQuery": f"{query} in {location}",
        "maxResultCount": min(limit, 20),  # API caps a single page at 20
    }

    leads: list[Lead] = []
    resp = requests.post(PLACES_SEARCH_URL, headers=headers, json=body, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    for place in data.get("places", [])[:limit]:
        website = place.get("websiteUri")
        leads.append(
            Lead(
                name=place.get("displayName", {}).get("text", "Unknown"),
                place_id=place["id"],
                address=place.get("formattedAddress", ""),
                phone=place.get("nationalPhoneNumber", ""),
                website=website,
                rating=place.get("rating"),
                review_count=place.get("userRatingCount"),
                category=", ".join(place.get("types", [])[:3]),
                has_website=bool(website),
            )
        )

    return leads
