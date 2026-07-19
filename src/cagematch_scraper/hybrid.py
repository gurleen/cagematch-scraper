"""Hybrid fetcher: patchright solves Sucuri, then httpx reuses the cleared session.

Sucuri's challenge cookie (`sucuri_cloudproxy_uuid_*`, ~24h TTL) is bound to the
requesting **exit IP and User-Agent**. So after one real-browser bootstrap that solves
the challenge, every further page can be fetched with a plain HTTP client — as long as
that client presents the same cookie, the browser's exact User-Agent, and comes from
the same IP.

Proxy-Cheap's `_session-<id>_ttl-<minutes>` credential suffix turns its rotating
residential endpoint into a sticky session. HybridFetcher creates one such session,
uses it for both patchright and httpx, then proactively switches to a fresh session/IP
after a configured request budget or before its TTL expires. Every switch gets a new
Sucuri cookie via patchright before HTTP scraping continues.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time

import httpx
from patchright.async_api import async_playwright

from .browser import _route_filter, is_blocked_page, is_challenge_page
from .config import Settings

logger = logging.getLogger(__name__)

_MAX_FETCH_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 3.0
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_SESSION_REJECTION_STATUS = {403, 429}
_CHALLENGE_WAIT_ROUNDS = 5
_CHALLENGE_WAIT_MS = 2000

# When a re-bootstrap itself fails, the exit IP is likely rate-limited or blocked by
# Sucuri; continuing to hammer it only prolongs the block. Consecutive bootstrap
# failures back off exponentially: 30s, 60s, 120s, ... capped at 10 minutes.
_BOOTSTRAP_COOLDOWN_BASE_SECONDS = 30.0
_BOOTSTRAP_COOLDOWN_MAX_SECONDS = 600.0


def _proxy_url(proxy: dict[str, str]) -> str:
    """Convert a playwright proxy dict to an httpx proxy URL with inline auth."""
    server = proxy["server"]
    scheme, _, hostport = server.partition("://")
    username = proxy.get("username")
    password = proxy.get("password")
    if username and password:
        return f"{scheme}://{username}:{password}@{hostport}"
    return server


_SESSION_SUFFIX_RE = re.compile(r"_session-[^_]+_ttl-\d+$")


def _with_sticky_session(
    proxy: dict[str, str], session_id: str, ttl_minutes: int
) -> dict[str, str]:
    """Return a Proxy-Cheap credential pinned to one exit IP for the given TTL."""
    password = _SESSION_SUFFIX_RE.sub("", proxy.get("password", ""))
    return {
        **proxy,
        "password": f"{password}_session-{session_id}_ttl-{ttl_minutes}",
    }


class HybridFetcher:
    """Async context manager with the same fetch/throttle/retry surface as
    `BrowserManager` and `HttpClient`, so `runner.py` can swap it in per spider.

    Lifecycle: `__aenter__` runs one patchright bootstrap (launch → solve challenge →
    export cookies + User-Agent → close browser), then serves every `fetch()` over
    httpx. If a response comes back as a challenge/block page (cookie expired or
    invalidated mid-run), the session is re-bootstrapped and the request retried.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        explicit_proxy = settings.proxy_dict()
        self._session_proxy_bases = (
            [explicit_proxy] if explicit_proxy is not None else settings.load_proxy_pool()
        )
        self._base_proxy_index = 0
        self._uses_rotating_sessions = bool(self._session_proxy_bases)
        self._proxy = (
            None if self._uses_rotating_sessions else settings.load_static_proxy()
        )
        self._client: httpx.AsyncClient | None = None
        self._last_request_at: float = 0.0
        self._throttle_lock = asyncio.Lock()
        self._bootstrap_lock = asyncio.Lock()
        self._state_condition = asyncio.Condition()
        self._session_generation = 0
        self._requests_in_session = 0
        self._active_requests = 0
        self._rotating = False
        self._session_deadline = float("inf")
        self._bootstrap_failures = 0
        self._cooldown_until = 0.0

    async def __aenter__(self) -> "HybridFetcher":
        if self._uses_rotating_sessions:
            logger.info(
                "Hybrid fetcher using sticky proxy sessions (%d requests or %d minutes each)",
                self._settings.proxy_session_max_requests,
                self._settings.proxy_session_ttl_minutes,
            )
        elif self._proxy is not None:
            logger.info("Hybrid fetcher using legacy static proxy %s", self._proxy["server"])
        else:
            logger.info("Hybrid fetcher running without a proxy (direct connection)")
        await self._rotate_session(self._session_generation, "initial bootstrap")
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _next_proxy(self) -> dict[str, str] | None:
        if not self._uses_rotating_sessions:
            return self._proxy
        base = self._session_proxy_bases[
            self._base_proxy_index % len(self._session_proxy_bases)
        ]
        self._base_proxy_index += 1
        return _with_sticky_session(
            base,
            secrets.token_hex(8),
            max(1, self._settings.proxy_session_ttl_minutes),
        )

    async def _bootstrap(self) -> None:
        """Solve the Sucuri challenge in a throwaway browser and rebuild the httpx
        client with the resulting cookies and the browser's User-Agent.
        """
        url = self._settings.base_url
        proxy = self._next_proxy()
        logger.info(
            "Bootstrapping Sucuri session via patchright (%s, proxy %s)",
            url,
            proxy["server"] if proxy else "direct",
        )

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=self._settings.headless,
                channel=self._settings.channel,
                proxy=proxy,
            )
            try:
                context = await browser.new_context()
                if self._settings.block_resources:
                    await context.route("**/*", _route_filter)
                page = await context.new_page()
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self._settings.nav_timeout_ms,
                )
                html = await page.content()
                rounds = 0
                while is_challenge_page(html) and rounds < _CHALLENGE_WAIT_ROUNDS:
                    logger.info("Sucuri challenge detected during bootstrap, waiting...")
                    await page.wait_for_timeout(_CHALLENGE_WAIT_MS)
                    html = await page.content()
                    rounds += 1
                if is_challenge_page(html) or is_blocked_page(html):
                    raise RuntimeError(f"Sucuri challenge did not clear during bootstrap of {url}")

                cookies = await context.cookies()
                user_agent = await page.evaluate("() => navigator.userAgent")
            finally:
                await browser.close()

        jar = httpx.Cookies()
        for cookie in cookies:
            jar.set(
                cookie["name"],
                cookie["value"],
                domain=cookie.get("domain") or "",
                path=cookie.get("path") or "/",
            )

        if self._client is not None:
            await self._client.aclose()
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            cookies=jar,
            proxy=_proxy_url(proxy) if proxy else None,
            trust_env=False,
            follow_redirects=True,
            timeout=httpx.Timeout(self._settings.nav_timeout_ms / 1000.0),
        )
        self._proxy = proxy
        self._session_generation += 1
        self._requests_in_session = 0
        self._session_deadline = (
            time.monotonic()
            + max(1, self._settings.proxy_session_ttl_minutes) * 60
            - _CHALLENGE_WAIT_MS / 1000
            if self._uses_rotating_sessions
            else float("inf")
        )
        logger.info(
            "Sucuri session %d established (cookies: %s)",
            self._session_generation,
            ", ".join(c["name"] for c in cookies) or "none",
        )

    async def _rotate_session(self, seen_generation: int, reason: str) -> None:
        """Wait for in-flight HTTP requests, then bootstrap a fresh proxy/IP session."""
        async with self._bootstrap_lock:
            if self._session_generation != seen_generation:
                return

            async with self._state_condition:
                self._rotating = True
                while self._active_requests:
                    await self._state_condition.wait()

            try:
                remaining = self._cooldown_until - time.monotonic()
                if remaining > 0:
                    logger.warning(
                        "Bootstrap cooling down for %.0fs after %d consecutive failure(s)",
                        remaining,
                        self._bootstrap_failures,
                    )
                    await asyncio.sleep(remaining)
                logger.info("Rotating proxy/Sucuri session: %s", reason)
                try:
                    await self._bootstrap()
                except Exception:
                    self._bootstrap_failures += 1
                    cooldown = min(
                        _BOOTSTRAP_COOLDOWN_BASE_SECONDS
                        * 2 ** (self._bootstrap_failures - 1),
                        _BOOTSTRAP_COOLDOWN_MAX_SECONDS,
                    )
                    self._cooldown_until = time.monotonic() + cooldown
                    logger.warning(
                        "Bootstrap failed (%d in a row); pausing re-bootstraps for %.0fs",
                        self._bootstrap_failures,
                        cooldown,
                    )
                    raise
                else:
                    self._bootstrap_failures = 0
                    self._cooldown_until = 0.0
            finally:
                async with self._state_condition:
                    self._rotating = False
                    self._state_condition.notify_all()

    async def _rebootstrap(self, seen_generation: int) -> None:
        """Compatibility wrapper for rejection-triggered session rotation."""
        await self._rotate_session(seen_generation, "Sucuri rejected the current session")

    async def _reserve_request(self) -> tuple[httpx.AsyncClient, int]:
        """Reserve one request from the current sticky-IP budget.

        Rotation waits until prior requests finish before closing their client, and
        callers queue behind `_rotating` until the new IP has passed Sucuri.
        """
        while True:
            async with self._state_condition:
                while self._rotating:
                    await self._state_condition.wait()
                exhausted = (
                    self._uses_rotating_sessions
                    and self._requests_in_session
                    >= max(1, self._settings.proxy_session_max_requests)
                )
                expired = (
                    self._uses_rotating_sessions
                    and time.monotonic() >= self._session_deadline
                )
                if not exhausted and not expired:
                    assert self._client is not None, "HybridFetcher not entered"
                    self._requests_in_session += 1
                    self._active_requests += 1
                    return self._client, self._session_generation
                generation = self._session_generation

            reason = "request budget exhausted" if exhausted else "proxy session TTL expiring"
            await self._rotate_session(generation, reason)

    async def _release_request(self) -> None:
        async with self._state_condition:
            self._active_requests -= 1
            self._state_condition.notify_all()

    async def _throttle(self) -> None:
        async with self._throttle_lock:
            elapsed = time.monotonic() - self._last_request_at
            remaining = self._settings.request_delay - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)
            self._last_request_at = time.monotonic()

    async def fetch(self, url: str) -> str:
        last_error: Exception | None = None
        for attempt in range(1, _MAX_FETCH_ATTEMPTS + 1):
            await self._throttle()
            try:
                client, generation = await self._reserve_request()
                try:
                    response = await client.get(url)
                finally:
                    await self._release_request()
                text = response.text
                if (
                    response.status_code in _SESSION_REJECTION_STATUS
                    or is_challenge_page(text)
                    or is_blocked_page(text)
                ):
                    logger.info(
                        "Proxy/Sucuri session rejected for %s (HTTP %d), rotating",
                        url,
                        response.status_code,
                    )
                    await self._rotate_session(generation, f"HTTP {response.status_code} or challenge")
                    raise RuntimeError(f"Sucuri session rejected for {url} (rotated)")
                if response.status_code in _RETRYABLE_STATUS:
                    raise httpx.HTTPStatusError(
                        f"retryable status {response.status_code} for {url}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return text
            except (httpx.HTTPError, httpx.TimeoutException, RuntimeError) as error:
                last_error = error
                logger.warning(
                    "Hybrid fetch attempt %d/%d failed for %s: %s",
                    attempt,
                    _MAX_FETCH_ATTEMPTS,
                    url,
                    error,
                )
                if attempt < _MAX_FETCH_ATTEMPTS:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS * attempt)

        assert last_error is not None
        raise last_error
