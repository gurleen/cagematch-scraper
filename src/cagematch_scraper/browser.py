"""Patchright-backed browser manager, aware of the Sucuri CloudProxy JS challenge."""

from __future__ import annotations

import asyncio
import logging
import time

from patchright.async_api import Browser, BrowserContext, Error as PlaywrightError, Page, async_playwright

from .config import ProxyPool, Settings

logger = logging.getLogger(__name__)

_MAX_FETCH_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 3.0

_CHALLENGE_MARKERS = (
    "You are being redirected",
    "Sucuri WebSite Firewall",
    "sucuri_cloudproxy_js",
)

# Sucuri's hard IP block ("Access Denied - Website Firewall", Block ID BLACKnn). Unlike
# the JS challenge above it never clears by waiting — but with a rotating proxy a fresh
# connection often lands on a clean exit IP, so it's treated as a retryable fetch error
# (a new page per attempt = a new exit) rather than accepted as valid page content.
_BLOCK_MARKERS = (
    "Access Denied - Website Firewall",
    "Website Security - Access Denied",
)

_BLOCKED_RESOURCE_TYPES = {"image", "media", "font", "stylesheet"}


def _route_filter(route) -> object:
    if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
        return route.abort()
    return route.continue_()


def _next_proxy(settings: Settings, pool: list[dict[str, str]]) -> dict[str, str] | None:
    """Pick the next proxy from `pool`, persisting a cursor so successive CLI runs rotate."""
    if not pool:
        return None
    cursor_path = settings.output_dir / ".proxy_cursor"
    try:
        index = int(cursor_path.read_text(encoding="utf-8").strip()) % len(pool)
    except (OSError, ValueError):
        index = 0
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    cursor_path.write_text(str((index + 1) % len(pool)), encoding="utf-8")
    return pool[index]


class BrowserManager:
    """Async context manager owning a single browser context for a scrape run."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._last_request_at: float = 0.0
        self._throttle_lock = asyncio.Lock()

    async def __aenter__(self) -> "BrowserManager":
        self._playwright = await async_playwright().start()
        proxy = self._settings.proxy_dict()
        if proxy is None:
            pool = list(ProxyPool(self._settings.load_proxy_pool()))
            proxy = _next_proxy(self._settings, pool)
            if proxy is not None:
                logger.info("Using proxy %s (%d in pool)", proxy["server"], len(pool))
        launch_kwargs = dict(
            headless=self._settings.headless,
            channel=self._settings.channel,
            proxy=proxy,
        )
        if self._settings.user_data_dir is not None:
            self._settings.user_data_dir.mkdir(parents=True, exist_ok=True)
            self._context = await self._playwright.chromium.launch_persistent_context(
                str(self._settings.user_data_dir), **launch_kwargs
            )
            self._browser = None
        else:
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
            self._context = await self._browser.new_context()

        if self._settings.block_resources:
            await self._context.route("**/*", _route_filter)
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def _throttle(self) -> None:
        """Space out request *start* times by at least `request_delay`.

        Guarded by a lock so concurrent `fetch()` callers (see `runner.py`, which now
        runs fetches concurrently up to `settings.concurrency`) queue for their turn to
        start here without racing on `_last_request_at` — but each caller's actual
        `page.goto()`/`page.content()` work happens after this returns, outside the
        lock, so network wait time still overlaps across concurrent fetches.
        """
        async with self._throttle_lock:
            elapsed = time.monotonic() - self._last_request_at
            remaining = self._settings.request_delay - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)
            self._last_request_at = time.monotonic()

    @staticmethod
    def _is_challenge_page(html: str) -> bool:
        return any(marker in html for marker in _CHALLENGE_MARKERS)

    @staticmethod
    def _is_blocked_page(html: str) -> bool:
        return any(marker in html for marker in _BLOCK_MARKERS)

    async def fetch(self, url: str) -> str:
        """Navigate to url, ride out the Sucuri JS challenge if present, return HTML.

        Retries transient navigation errors (proxy tunnel hiccups, resets, timeouts)
        up to `_MAX_FETCH_ATTEMPTS` times with a fixed backoff, since a long unattended
        scrape shouldn't abort entirely over one flaky request.
        """
        assert self._context is not None, "BrowserManager not entered"

        last_error: PlaywrightError | None = None
        for attempt in range(1, _MAX_FETCH_ATTEMPTS + 1):
            await self._throttle()
            page: Page = await self._context.new_page()
            try:
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self._settings.nav_timeout_ms,
                )
                html = await page.content()

                if self._is_blocked_page(html):
                    # Retryable: raise so the attempt loop opens a fresh page (and, with
                    # a rotating proxy, a new exit IP) rather than returning the block page.
                    raise PlaywrightError(f"Sucuri firewall blocked {url} (retrying)")

                challenge_attempts = 0
                while self._is_challenge_page(html) and challenge_attempts < 5:
                    logger.info("Sucuri challenge detected for %s, waiting...", url)
                    await page.wait_for_timeout(2000)
                    html = await page.content()
                    challenge_attempts += 1

                if self._is_challenge_page(html):
                    raise RuntimeError(f"Sucuri challenge did not clear for {url}")

                return html
            except PlaywrightError as error:
                last_error = error
                logger.warning(
                    "Fetch attempt %d/%d failed for %s: %s",
                    attempt,
                    _MAX_FETCH_ATTEMPTS,
                    url,
                    error,
                )
                if attempt < _MAX_FETCH_ATTEMPTS:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS * attempt)
            finally:
                await page.close()

        assert last_error is not None
        raise last_error
