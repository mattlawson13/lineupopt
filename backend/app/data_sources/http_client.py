"""Shared HTTP plumbing for data sources: disk caching, per-host rate
limiting, and retry-with-backoff. Kept deliberately simple (no extra
service dependency) — swap for Redis-backed caching/rate-limiting later
without changing call sites.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

CACHE_DIR = Path(__file__).parent.parent.parent / ".cache" / "http"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_last_request_at: dict[str, float] = {}
# Guards the read-check-sleep-write sequence in _throttle below. Without
# this, concurrent callers (e.g. slate_builder._fetch_and_apply_weather's
# ThreadPoolExecutor, one thread per game) all read the same stale
# `_last_request_at[host]` before any of them writes it back, so the
# per-host spacing this class exists to enforce silently doesn't apply —
# confirmed live: 13 near-simultaneous weather calls all hit Open-Meteo in
# the same instant despite min_interval_seconds=0.5, and got 429'd as a
# result. Holding the lock across the sleep serializes same-host callers
# at exactly the intended rate instead of letting them race past it.
_throttle_lock = threading.Lock()


def _cache_key(url: str, params: dict | None) -> str:
    raw = url + json.dumps(params or {}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


class ThrottledClient:
    """A small httpx wrapper providing:
      - per-host minimum request spacing (rate limiting)
      - retry with exponential backoff on transient failures
      - a simple disk cache with a caller-specified TTL, so a flaky source
        doesn't take the whole slate build down and repeated calls (e.g.
        during dev) don't hammer the provider.
    """

    def __init__(self, min_interval_seconds: float = 1.0, timeout: float = 15.0, user_agent: str | None = None):
        self.min_interval_seconds = min_interval_seconds
        headers = {"User-Agent": user_agent or "LineupOpt/0.1 (+https://github.com/mattlawson13/lineupopt)"}
        self._client = httpx.Client(timeout=timeout, headers=headers, follow_redirects=True)

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        with _throttle_lock:
            now = time.monotonic()
            last = _last_request_at.get(host, 0.0)
            wait = self.min_interval_seconds - (now - last)
            if wait > 0:
                time.sleep(wait)
            _last_request_at[host] = time.monotonic()

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    )
    def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        self._throttle(url)
        resp = self._client.get(url, params=params)
        if resp.status_code >= 500:
            resp.raise_for_status()
        return resp

    def get_json(self, url: str, params: dict | None = None, cache_ttl_seconds: int = 300) -> tuple[dict | list, bool]:
        """Returns (payload, was_cache_hit)."""
        key = _cache_key(url, params)
        cache_file = CACHE_DIR / f"{key}.json"
        if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < cache_ttl_seconds:
            return json.loads(cache_file.read_text()), True

        resp = self._get(url, params)
        resp.raise_for_status()
        payload = resp.json()
        cache_file.write_text(json.dumps(payload))
        return payload, False

    def get_text(self, url: str, params: dict | None = None, cache_ttl_seconds: int = 3600) -> tuple[str, bool]:
        key = _cache_key(url, params)
        cache_file = CACHE_DIR / f"{key}.txt"
        if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < cache_ttl_seconds:
            return cache_file.read_text(), True

        resp = self._get(url, params)
        resp.raise_for_status()
        cache_file.write_text(resp.text)
        return resp.text, False

    def close(self) -> None:
        self._client.close()
