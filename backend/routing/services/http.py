"""Shared HTTP client: caching, polite per-host rate limiting and retries."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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
        self.reason = message


class RateLimited(UpstreamError):
    """HTTP 429. `retry_after` is the server's Retry-After in seconds, if it sent one."""

    def __init__(self, service: str, retry_after: float | None):
        super().__init__(service, "HTTP 429 (rate limited)")
        self.retry_after = retry_after


def _retry_after(resp: requests.Response) -> float | None:
    value = resp.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError):
            return None


# Never block a user request longer than this waiting on a Retry-After.
MAX_RETRY_AFTER_WAIT = 3.0


# Minimum seconds between requests per host (public-instance usage policies).
_MIN_INTERVAL = {
    "nominatim.openstreetmap.org": 1.05,
    "router.project-osrm.org": 0.25,
    "routing.openstreetmap.de": 0.25,
    "api.met.no": 0.06,  # MET Norway allows ~20 req/s per application
}
_last_call: dict[str, float] = {}
_lock = threading.Lock()
_session = requests.Session()


def _throttle(host: str) -> None:
    """Space requests to `host` at least its minimum interval apart (thread-safe).

    Each caller reserves the next free slot under the lock, then sleeps outside it, so
    parallel requests to one host queue up without blocking other hosts.
    """
    gap = _MIN_INTERVAL.get(host)
    if not gap:
        return
    with _lock:
        now = time.monotonic()
        slot = max(now, _last_call.get(host, 0.0) + gap)
        _last_call[host] = slot
    if slot > now:
        time.sleep(slot - now)


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
    last_exc: UpstreamError | None = None
    for attempt in range(retries + 1):
        _throttle(host)
        try:
            resp = _session.get(url, params=params, headers=headers, timeout=settings.HTTP_TIMEOUT)
        except requests.RequestException as exc:
            last_exc = UpstreamError(service, f"network error ({exc.__class__.__name__})")
        else:
            if resp.status_code == 429:
                wait = _retry_after(resp)
                last_exc = RateLimited(service, wait)
                # Honour a short Retry-After once; a long one means "come back later", so let
                # the caller switch to a fallback instead of making the user wait.
                if wait is None or wait > MAX_RETRY_AFTER_WAIT or attempt == retries:
                    raise last_exc
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                last_exc = UpstreamError(service, f"HTTP {resp.status_code}")
            elif resp.status_code >= 400:
                # Client errors won't succeed on retry; surface the upstream message.
                try:
                    body = resp.json()
                    detail = body.get("reason") or body.get("message") or resp.text[:200]
                except (ValueError, AttributeError):
                    detail = resp.text[:200]
                raise UpstreamError(service, f"HTTP {resp.status_code}: {detail}")
            else:
                try:
                    data = resp.json()
                except ValueError:
                    last_exc = UpstreamError(service, "invalid JSON response")
                else:
                    cache.set(key, data, ttl)
                    return data
        if attempt < retries:
            logger.warning("Retrying %s after error: %s", service, last_exc)
            time.sleep(0.8 * (2**attempt))
    assert last_exc is not None
    raise last_exc

