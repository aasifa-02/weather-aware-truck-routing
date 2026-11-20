"""Shared HTTP client: caching, polite per-host rate limiting and retries."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


class UpstreamError(Exception):
    """An external API failed or returned something unusable."""

    def __init__(self, service: str, message: str):
        super().__init__(f"{service}: {message}")
        self.service = service


# Minimum seconds between requests per host (public-instance usage policies).
_MIN_INTERVAL = {
    "nominatim.openstreetmap.org": 1.05,
    "router.project-osrm.org": 0.25,
}
_last_call: dict[str, float] = {}
_lock = threading.Lock()
_session = requests.Session()


def _throttle(host: str) -> None:
    gap = _MIN_INTERVAL.get(host)
    if not gap:
        return
    with _lock:
        wait = _last_call.get(host, 0.0) + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.monotonic()


def cache_key(prefix: str, payload: Any) -> str:
    digest = hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    return f"{prefix}:{digest}"


def get_json(
    service: str,
    url: str,
    params: dict[str, Any] | None = None,
    *,
    ttl: int = 3600,
    retries: int = 2,
) -> Any:
    """GET JSON with a cache in front. Errors raise UpstreamError (never cached)."""
    key = cache_key(service, [url, params])
    hit = cache.get(key)
    if hit is not None:
        return hit

    host = urlparse(url).netloc
    headers = {"User-Agent": settings.HTTP_USER_AGENT, "Accept": "application/json"}
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        _throttle(host)
        try:
            resp = _session.get(url, params=params, headers=headers, timeout=settings.HTTP_TIMEOUT)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise UpstreamError(service, f"HTTP {resp.status_code}")
            if resp.status_code >= 400:
                # Client errors won't succeed on retry; surface the upstream message.
                try:
                    detail = resp.json().get("reason") or resp.json().get("message") or resp.text[:200]
                except ValueError:
                    detail = resp.text[:200]
                raise UpstreamError(service, f"HTTP {resp.status_code}: {detail}")
            data = resp.json()
            cache.set(key, data, ttl)
            return data
        except UpstreamError as exc:
            last_exc = exc
            if "HTTP 4" in str(exc) and "429" not in str(exc):
                raise
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
        if attempt < retries:
            time.sleep(0.8 * (2**attempt))
            logger.warning("Retrying %s after error: %s", service, last_exc)
    raise UpstreamError(service, str(last_exc))
