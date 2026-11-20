"""Geocoding with Nominatim (primary) and Photon (fallback). Both free and keyless."""
from __future__ import annotations

import re
from dataclasses import dataclass

from django.conf import settings

from .http import UpstreamError, get_json

_LATLNG_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


@dataclass(frozen=True)
class Place:
    query: str
    name: str
    lat: float
    lng: float

    def as_dict(self) -> dict:
        return {"query": self.query, "name": self.name, "lat": self.lat, "lng": self.lng}


class GeocodeError(Exception):
    pass


def geocode(query: str) -> Place:
    query = query.strip()
    m = _LATLNG_RE.match(query)
    if m:
        lat, lng = float(m.group(1)), float(m.group(2))
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            return Place(query, f"{lat:.4f}, {lng:.4f}", lat, lng)

    try:
        results = get_json(
            "nominatim",
            settings.NOMINATIM_URL,
            {"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "us,ca,mx"},
            ttl=60 * 60 * 24 * 30,
        )
        if results:
            r = results[0]
            return Place(query, r.get("display_name", query), float(r["lat"]), float(r["lon"]))
    except UpstreamError:
        pass  # fall through to Photon

    try:
        data = get_json("photon", settings.PHOTON_URL, {"q": query, "limit": 1}, ttl=60 * 60 * 24 * 30)
    except UpstreamError as exc:
        raise GeocodeError(f"Geocoding services unavailable ({exc}).") from exc
    feats = data.get("features") or []
    if not feats:
        raise GeocodeError(f'Could not find a location for "{query}".')
    props = feats[0]["properties"]
    lng, lat = feats[0]["geometry"]["coordinates"]
    name = ", ".join(p for p in (props.get("name"), props.get("state"), props.get("country")) if p)
    return Place(query, name or query, float(lat), float(lng))
