"""HTTP with a rate limit, retries with backoff and a timeout (D3). Network is injectable for tests."""

import time
from collections.abc import Callable
from typing import Any

import httpx

from arthashreni.ingestion.sources.contract import SourceUnavailable

OnCall = Callable[[bool, int | None, str], None]  # ok, http status, detail (never a URL or a symbol)


class Http:
    def __init__(
        self,
        source_id: str,
        *,
        client: httpx.Client | None = None,
        min_interval: float = 0.0,
        retries: int = 3,
        backoff: float = 1.0,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        on_call: OnCall | None = None,
    ) -> None:
        self.source_id = source_id
        self.client = client or httpx.Client(follow_redirects=True)
        self.min_interval = min_interval
        self.retries = retries
        self.backoff = backoff
        self.timeout = timeout
        self.headers = headers or {}
        self._sleep, self._clock = sleep, clock
        self._on_call = on_call
        self._last = float("-inf")

    def get(
        self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None
    ) -> bytes:
        last = "no attempt"
        for attempt in range(self.retries + 1):
            wait = self.min_interval - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = self._clock()
            status: int | None = None
            try:
                r = self.client.get(
                    url, params=params, headers={**self.headers, **(headers or {})}, timeout=self.timeout
                )
                status = r.status_code
                if status == 429 or status >= 500:
                    last = f"HTTP {status}"
                elif status >= 400:
                    self._record(False, status, f"HTTP {status}")
                    raise SourceUnavailable(f"{self.source_id}: HTTP {status}")  # client error: no retry
                else:
                    self._record(True, status, "ok")
                    return r.content
            except httpx.TransportError as exc:
                last = type(exc).__name__
            self._record(False, status, last)
            if attempt < self.retries:
                self._sleep(self.backoff * 2**attempt)
        raise SourceUnavailable(f"{self.source_id}: {last} after {self.retries + 1} attempts")

    def _record(self, ok: bool, status: int | None, detail: str) -> None:
        if self._on_call:
            self._on_call(ok, status, detail)
