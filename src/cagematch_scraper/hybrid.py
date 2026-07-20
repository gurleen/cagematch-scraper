"""Hybrid fetcher: patchright solves Sucuri, then httpx reuses the cleared session.

Sucuri's challenge cookie (`sucuri_cloudproxy_uuid_*`, ~24h TTL) is bound to the
requesting **exit IP and User-Agent**. So after one real-browser bootstrap that solves
the challenge, every further page can be fetched with a plain HTTP client — as long as
that client presents the same cookie, the browser's exact User-Agent, and comes from
the same IP.

Proxy-Cheap's `_session-<id>_ttl-<minutes>` credential suffix turns its rotating
residential endpoint into a sticky session. HybridFetcher keeps a **pool** of those
sessions (sized to `Settings.concurrency` when proxies are configured): each slot has
its own sticky exit IP, Sucuri cookie, httpx client, and request throttle. Concurrent
`fetch()` calls check out different slots so traffic is spread across proxies instead
of sharing one IP. Sessions rotate independently after a request budget / TTL / Sucuri
rejection, without pausing the rest of the pool.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx
from patchright.async_api import async_playwright

from .browser import _route_filter, is_blocked_page, is_challenge_page
from .config import Settings

AllocateProxy = Callable[[], Awaitable[dict[str, str] | None]]

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


@dataclass
class _ProxySession:
    """One sticky-IP Sucuri session: browser-bootstrapped cookies + httpx client."""

    slot: int
    settings: Settings
    uses_rotating_sessions: bool
    # When False (direct/static single-slot pool), multiple fetches may share this
    # session concurrently. When True, HybridFetcher checkouts are exclusive.
    exclusive: bool
    allocate_proxy: AllocateProxy = field(repr=False)
    client: httpx.AsyncClient | None = None
    proxy: dict[str, str] | None = None
    generation: int = 0
    requests_in_session: int = 0
    session_deadline: float = float("inf")
    active_requests: int = 0
    rotating: bool = False
    last_request_at: float = 0.0
    bootstrap_failures: int = 0
    cooldown_until: float = 0.0
    throttle_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    bootstrap_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    state: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def aclose(self) -> None:
        client = self.client
        self.client = None
        if client is not None and hasattr(client, "aclose"):
            await client.aclose()

    async def bootstrap(self) -> None:
        """Solve Sucuri in a throwaway browser and rebuild this slot's httpx client."""
        url = self.settings.base_url
        proxy = await self.allocate_proxy()
        logger.info(
            "Bootstrapping Sucuri session slot=%d via patchright (%s, proxy %s)",
            self.slot,
            url,
            proxy["server"] if proxy else "direct",
        )

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=self.settings.headless,
                channel=self.settings.channel,
                proxy=proxy,
            )
            try:
                context = await browser.new_context()
                if self.settings.block_resources:
                    await context.route("**/*", _route_filter)
                page = await context.new_page()
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.settings.nav_timeout_ms,
                )
                html = await page.content()
                rounds = 0
                while is_challenge_page(html) and rounds < _CHALLENGE_WAIT_ROUNDS:
                    logger.info(
                        "Sucuri challenge detected during bootstrap slot=%d, waiting...",
                        self.slot,
                    )
                    await page.wait_for_timeout(_CHALLENGE_WAIT_MS)
                    html = await page.content()
                    rounds += 1
                if is_challenge_page(html) or is_blocked_page(html):
                    raise RuntimeError(
                        f"Sucuri challenge did not clear during bootstrap of {url} "
                        f"(slot={self.slot})"
                    )

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

        await self.aclose()
        self.client = httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            cookies=jar,
            proxy=_proxy_url(proxy) if proxy else None,
            trust_env=False,
            follow_redirects=True,
            timeout=httpx.Timeout(self.settings.nav_timeout_ms / 1000.0),
        )
        self.proxy = proxy
        self.generation += 1
        self.requests_in_session = 0
        self.session_deadline = (
            time.monotonic()
            + max(1, self.settings.proxy_session_ttl_minutes) * 60
            - _CHALLENGE_WAIT_MS / 1000
            if self.uses_rotating_sessions
            else float("inf")
        )
        logger.info(
            "Sucuri session slot=%d generation=%d established (cookies: %s)",
            self.slot,
            self.generation,
            ", ".join(c["name"] for c in cookies) or "none",
        )

    async def rotate(self, reason: str) -> None:
        """Drain in-flight requests on this slot, then bootstrap a fresh sticky IP."""
        async with self.bootstrap_lock:
            async with self.state:
                self.rotating = True
                while self.active_requests:
                    await self.state.wait()

            try:
                remaining = self.cooldown_until - time.monotonic()
                if remaining > 0:
                    logger.warning(
                        "Bootstrap cooling down for %.0fs on slot=%d after %d failure(s)",
                        remaining,
                        self.slot,
                        self.bootstrap_failures,
                    )
                    await asyncio.sleep(remaining)
                logger.info(
                    "Rotating proxy/Sucuri session slot=%d: %s", self.slot, reason
                )
                try:
                    await self.bootstrap()
                except Exception:
                    self.bootstrap_failures += 1
                    cooldown = min(
                        _BOOTSTRAP_COOLDOWN_BASE_SECONDS
                        * 2 ** (self.bootstrap_failures - 1),
                        _BOOTSTRAP_COOLDOWN_MAX_SECONDS,
                    )
                    self.cooldown_until = time.monotonic() + cooldown
                    logger.warning(
                        "Bootstrap failed on slot=%d (%d in a row); pause %.0fs",
                        self.slot,
                        self.bootstrap_failures,
                        cooldown,
                    )
                    raise
                else:
                    self.bootstrap_failures = 0
                    self.cooldown_until = 0.0
            finally:
                async with self.state:
                    self.rotating = False
                    self.state.notify_all()

    def _needs_rotate(self) -> tuple[bool, str]:
        if not self.uses_rotating_sessions:
            return False, ""
        if self.requests_in_session >= max(1, self.settings.proxy_session_max_requests):
            return True, "request budget exhausted"
        if time.monotonic() >= self.session_deadline:
            return True, "proxy session TTL expiring"
        return False, ""

    async def throttle(self) -> None:
        """Space this slot's request starts by a sampled delay from settings."""
        async with self.throttle_lock:
            elapsed = time.monotonic() - self.last_request_at
            remaining = self.settings.next_request_delay() - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)
            self.last_request_at = time.monotonic()

    async def reserve(self) -> tuple[httpx.AsyncClient, int]:
        """Reserve one in-flight request on this slot, rotating first if needed."""
        while True:
            async with self.state:
                while self.rotating:
                    await self.state.wait()
                if self.exclusive and self.active_requests:
                    await self.state.wait()
                    continue
                needs_rotate, reason = self._needs_rotate()
                if not needs_rotate:
                    if self.client is None:
                        raise RuntimeError(
                            f"Proxy session slot={self.slot} has no httpx client"
                        )
                    self.active_requests += 1
                    self.requests_in_session += 1
                    return self.client, self.generation

            await self.rotate(reason)

    async def release(self) -> None:
        async with self.state:
            self.active_requests -= 1
            self.state.notify_all()


class HybridFetcher:
    """Async context manager with the same fetch/throttle/retry surface as
    `BrowserManager` and `HttpClient`, so `runner.py` can swap it in per spider.

    With proxy credentials configured, `__aenter__` warms a pool of sticky sessions
    (size = `Settings.concurrency`), each with its own exit IP. Concurrent fetches
    check out different slots. Without proxies, the pool is a single shared session
    (same behavior as before, minus cross-request rotation pauses for other IPs).
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        explicit_proxy = settings.proxy_dict()
        self._session_proxy_bases = (
            [explicit_proxy] if explicit_proxy is not None else settings.load_proxy_pool()
        )
        self._base_proxy_index = 0
        self._proxy_lock = asyncio.Lock()
        self._uses_rotating_sessions = bool(self._session_proxy_bases)
        self._static_proxy = (
            None if self._uses_rotating_sessions else settings.load_static_proxy()
        )
        self._sessions: list[_ProxySession] = []
        self._queue: asyncio.Queue[_ProxySession] | None = None

    def _pool_size(self) -> int:
        if self._uses_rotating_sessions:
            return max(1, self._settings.concurrency)
        return 1

    async def _allocate_proxy(self) -> dict[str, str] | None:
        if not self._uses_rotating_sessions:
            return self._static_proxy
        async with self._proxy_lock:
            base = self._session_proxy_bases[
                self._base_proxy_index % len(self._session_proxy_bases)
            ]
            self._base_proxy_index += 1
            return _with_sticky_session(
                base,
                secrets.token_hex(8),
                max(1, self._settings.proxy_session_ttl_minutes),
            )

    async def __aenter__(self) -> "HybridFetcher":
        size = self._pool_size()
        exclusive = size > 1
        if self._uses_rotating_sessions:
            logger.info(
                "Hybrid fetcher session pool size=%d "
                "(%d requests or %d minutes per sticky session)",
                size,
                self._settings.proxy_session_max_requests,
                self._settings.proxy_session_ttl_minutes,
            )
        elif self._static_proxy is not None:
            logger.info(
                "Hybrid fetcher using legacy static proxy %s",
                self._static_proxy["server"],
            )
        else:
            logger.info("Hybrid fetcher running without a proxy (direct connection)")

        self._sessions = [
            _ProxySession(
                slot=index,
                settings=self._settings,
                uses_rotating_sessions=self._uses_rotating_sessions,
                exclusive=exclusive,
                allocate_proxy=self._allocate_proxy,
            )
            for index in range(size)
        ]
        # Warm every slot up front so the first wave of fetches is already parallel.
        await asyncio.gather(
            *(session.rotate(f"initial bootstrap slot={session.slot}") for session in self._sessions)
        )
        if exclusive:
            self._queue = asyncio.Queue()
            for session in self._sessions:
                self._queue.put_nowait(session)
        else:
            self._queue = None
        return self

    async def __aexit__(self, *exc_info) -> None:
        await asyncio.gather(*(session.aclose() for session in self._sessions))
        self._sessions.clear()
        self._queue = None

    async def _checkout(self) -> _ProxySession:
        if self._queue is not None:
            return await self._queue.get()
        return self._sessions[0]

    def _checkin(self, session: _ProxySession) -> None:
        if self._queue is not None:
            self._queue.put_nowait(session)

    async def fetch(self, url: str) -> str:
        last_error: Exception | None = None
        for attempt in range(1, _MAX_FETCH_ATTEMPTS + 1):
            session = await self._checkout()
            try:
                await session.throttle()
                try:
                    client, generation = await session.reserve()
                    try:
                        response = await client.get(url)
                    finally:
                        await session.release()
                    text = response.text
                    if (
                        response.status_code in _SESSION_REJECTION_STATUS
                        or is_challenge_page(text)
                        or is_blocked_page(text)
                    ):
                        logger.info(
                            "Proxy/Sucuri session rejected for %s (HTTP %d), "
                            "rotating slot=%d",
                            url,
                            response.status_code,
                            session.slot,
                        )
                        await session.rotate(
                            f"HTTP {response.status_code} or challenge"
                        )
                        raise RuntimeError(
                            f"Sucuri session rejected for {url} (rotated slot={session.slot})"
                        )
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
                        "Hybrid fetch attempt %d/%d failed for %s (slot=%d): %s",
                        attempt,
                        _MAX_FETCH_ATTEMPTS,
                        url,
                        session.slot,
                        error,
                    )
                    if attempt < _MAX_FETCH_ATTEMPTS:
                        await asyncio.sleep(_RETRY_BACKOFF_SECONDS * attempt)
            finally:
                self._checkin(session)

        assert last_error is not None
        raise last_error

    # --- test helpers / thin compatibility shims -------------------------------

    @property
    def _client(self) -> httpx.AsyncClient | None:
        return self._sessions[0].client if self._sessions else None

    @_client.setter
    def _client(self, value: httpx.AsyncClient | None) -> None:
        if not self._sessions:
            exclusive = self._pool_size() > 1
            self._sessions = [
                _ProxySession(
                    slot=0,
                    settings=self._settings,
                    uses_rotating_sessions=self._uses_rotating_sessions,
                    exclusive=exclusive,
                    allocate_proxy=self._allocate_proxy,
                )
            ]
        self._sessions[0].client = value

    @property
    def _session_generation(self) -> int:
        return self._sessions[0].generation if self._sessions else 0

    @_session_generation.setter
    def _session_generation(self, value: int) -> None:
        if not self._sessions:
            self._client = None  # ensures slot 0 exists
        self._sessions[0].generation = value

    @property
    def _requests_in_session(self) -> int:
        return self._sessions[0].requests_in_session if self._sessions else 0

    @_requests_in_session.setter
    def _requests_in_session(self, value: int) -> None:
        if not self._sessions:
            self._client = None
        self._sessions[0].requests_in_session = value

    @property
    def _bootstrap_failures(self) -> int:
        return self._sessions[0].bootstrap_failures if self._sessions else 0

    @_bootstrap_failures.setter
    def _bootstrap_failures(self, value: int) -> None:
        if not self._sessions:
            self._client = None
        self._sessions[0].bootstrap_failures = value

    @property
    def _cooldown_until(self) -> float:
        return self._sessions[0].cooldown_until if self._sessions else 0.0

    @_cooldown_until.setter
    def _cooldown_until(self, value: float) -> None:
        if not self._sessions:
            self._client = None
        self._sessions[0].cooldown_until = value

    async def _bootstrap(self) -> None:
        if not self._sessions:
            self._client = None
        await self._sessions[0].bootstrap()

    async def _rotate_session(self, seen_generation: int, reason: str) -> None:
        if not self._sessions:
            self._client = None
        session = self._sessions[0]
        if session.generation != seen_generation:
            return
        await session.rotate(reason)

    async def _rebootstrap(self, seen_generation: int) -> None:
        await self._rotate_session(seen_generation, "Sucuri rejected the current session")

    async def _reserve_request(self) -> tuple[httpx.AsyncClient, int]:
        if not self._sessions:
            self._client = None
        return await self._sessions[0].reserve()

    async def _release_request(self) -> None:
        if self._sessions:
            await self._sessions[0].release()
