from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests

from .cache import FileCache


class ApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class ApiResult:
    payload: Any
    source: str
    url: str


class HttpClient:
    def __init__(
        self,
        cache: FileCache,
        *,
        timeout: float = 15.0,
        retries: int = 3,
        user_agent: str = "skyflip/0.1",
    ) -> None:
        self.cache = cache
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})

    def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cache_key: str | None = None,
        use_cache: bool = True,
    ) -> ApiResult:
        key = cache_key or url
        cached = self.cache.get(key) if use_cache else None
        if cached is not None:
            return ApiResult(payload=cached.payload, source="cache", url=url)

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(url, timeout=self.timeout, headers=headers)
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise ApiError(f"HTTP {response.status_code} for {url}")
                if 400 <= response.status_code < 500:
                    response.raise_for_status()
                response.raise_for_status()
                payload = response.json()
                if use_cache:
                    self.cache.set(key, payload)
                return ApiResult(payload=payload, source="live", url=url)
            except (requests.RequestException, ValueError, ApiError) as exc:
                last_error = exc
                if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code not in {429, 500, 502, 503, 504}:
                    break
                if isinstance(exc, ValueError):
                    break
                if attempt >= self.retries:
                    break
                sleep_seconds = min(8.0, 0.5 * (2**attempt))
                time.sleep(sleep_seconds)
        raise ApiError(str(last_error) if last_error else f"Failed to fetch {url}")
