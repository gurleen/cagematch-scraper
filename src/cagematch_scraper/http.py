"""Plain HTTP fetcher for sites that don't need a browser (e.g. The Smackdown Hotel)."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from .config import Settings

logger = logging.getLogger(__name__)
# httpx logs every request at INFO; keep our own fetch logging instead.
logging.getLogger("httpx").setLevel(logging.WARNING)

_MAX_FETCH_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 3.0
_USER_AGENT = "cagematch-scraper/0.1 (+https://github.com/gurleen/cagematch-scraper)"
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def fetch_text_sync(url: str, *, timeout: float = 30.0) -> str:
    """Blocking GET for spider bootstrap (sitemap / index discovery in `start_requests`)."""
    with httpx.Client(
        headers={"User-Agent": _USER_AGENT},
        follow_redirects=True,
        timeout=timeout,
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


class HttpClient:
    """Async context manager that fetches pages over HTTP with the same throttle/retry
    surface as `BrowserManager`, so the runner can swap backends per spider.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: httpx.AsyncClient | None = None
        self._last_request_at: float = 0.0
        self._throttle_lock = asyncio.Lock()

    async def __aenter__(self) -> "HttpClient":
        timeout = httpx.Timeout(self._settings.nav_timeout_ms / 1000.0)
        self._client = httpx.AsyncClient(
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
            timeout=timeout,
        )
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _throttle(self) -> None:
        async with self._throttle_lock:
            elapsed = time.monotonic() - self._last_request_at
            remaining = self._settings.request_delay - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)
            self._last_request_at = time.monotonic()

    async def fetch(self, url: str) -> str:
        assert self._client is not None, "HttpClient not entered"

        last_error: Exception | None = None
        for attempt in range(1, _MAX_FETCH_ATTEMPTS + 1):
            await self._throttle()
            try:
                response = await self._client.get(url)
                if response.status_code in _RETRYABLE_STATUS:
                    raise httpx.HTTPStatusError(
                        f"retryable status {response.status_code} for {url}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return response.text
            except (httpx.HTTPError, httpx.TimeoutException) as error:
                last_error = error
                logger.warning(
                    "HTTP fetch attempt %d/%d failed for %s: %s",
                    attempt,
                    _MAX_FETCH_ATTEMPTS,
                    url,
                    error,
                )
                if attempt < _MAX_FETCH_ATTEMPTS:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS * attempt)

        assert last_error is not None
        raise last_error
