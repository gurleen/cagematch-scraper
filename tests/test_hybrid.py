import asyncio
import time

import pytest

from cagematch_scraper import hybrid
from cagematch_scraper.config import Settings
from cagematch_scraper.hybrid import HybridFetcher, _ProxySession, _with_sticky_session


def _fetcher(**overrides: object) -> HybridFetcher:
    settings = Settings(_env_file=None, proxy_list_file=None, **overrides)
    return HybridFetcher(settings)


def _ensure_slot(fetcher: HybridFetcher) -> _ProxySession:
    """Materialize slot 0 without entering the fetcher (for unit tests)."""
    fetcher._client = object()  # type: ignore[assignment]
    return fetcher._sessions[0]


def test_proxy_cheap_sticky_session_credential() -> None:
    proxy = {
        "server": "http://thehub.proxy-cheap.com:8080",
        "username": "user",
        "password": "secret",
    }

    session = _with_sticky_session(proxy, "abc123", 10)

    assert session["password"] == "secret_session-abc123_ttl-10"
    assert proxy["password"] == "secret"


def test_sticky_session_suffix_is_replaced_not_nested() -> None:
    proxy = {
        "server": "http://thehub.proxy-cheap.com:8080",
        "username": "user",
        "password": "secret_session-old_ttl-5",
    }

    session = _with_sticky_session(proxy, "new", 10)

    assert session["password"] == "secret_session-new_ttl-10"


def test_pool_size_follows_concurrency_when_proxies_configured() -> None:
    fetcher = _fetcher(
        proxy_server="http://thehub.proxy-cheap.com:8080",
        proxy_username="user",
        proxy_password="secret",
        concurrency=8,
    )
    assert fetcher._pool_size() == 8


def test_pool_size_is_one_without_proxies() -> None:
    fetcher = _fetcher(concurrency=8)
    assert fetcher._pool_size() == 1


def test_request_budget_rotates_before_reserving_next_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetcher = _fetcher(
        proxy_server="http://thehub.proxy-cheap.com:8080",
        proxy_username="user",
        proxy_password="secret",
        proxy_session_max_requests=2,
    )
    session = _ensure_slot(fetcher)
    session.generation = 1
    session.requests_in_session = 2

    async def fake_bootstrap() -> None:
        session.generation += 1
        session.requests_in_session = 0
        session.client = object()  # type: ignore[assignment]

    monkeypatch.setattr(session, "bootstrap", fake_bootstrap)

    async def run() -> None:
        _, generation = await session.reserve()
        assert generation == 2
        assert session.requests_in_session == 1
        await session.release()

    asyncio.run(run())


def test_failed_rebootstrap_sets_growing_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hybrid, "_BOOTSTRAP_COOLDOWN_BASE_SECONDS", 0.01)
    fetcher = _fetcher()
    session = _ensure_slot(fetcher)

    async def failing_bootstrap() -> None:
        raise RuntimeError("challenge did not clear")

    monkeypatch.setattr(session, "bootstrap", failing_bootstrap)

    async def run() -> None:
        for expected_failures in (1, 2, 3):
            with pytest.raises(RuntimeError):
                await session.rotate("test")
            assert session.bootstrap_failures == expected_failures
            assert session.cooldown_until > time.monotonic() - 1

    asyncio.run(run())
    # Exponential: third failure's cooldown is 4x base.
    assert session.cooldown_until - time.monotonic() <= 0.04 + 0.01


def test_successful_rebootstrap_resets_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hybrid, "_BOOTSTRAP_COOLDOWN_BASE_SECONDS", 0.01)
    fetcher = _fetcher()
    session = _ensure_slot(fetcher)
    session.bootstrap_failures = 2
    session.cooldown_until = time.monotonic() + 0.01

    async def ok_bootstrap() -> None:
        session.generation += 1

    monkeypatch.setattr(session, "bootstrap", ok_bootstrap)
    asyncio.run(session.rotate("test"))

    assert session.bootstrap_failures == 0
    assert session.cooldown_until == 0.0


def test_rotate_skipped_when_generation_moved(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = _fetcher()
    session = _ensure_slot(fetcher)
    session.generation = 5

    async def unexpected_bootstrap() -> None:
        raise AssertionError("bootstrap should not run for a stale generation")

    monkeypatch.setattr(session, "bootstrap", unexpected_bootstrap)
    asyncio.run(fetcher._rotate_session(4, "stale"))


def test_exclusive_pool_checkouts_distinct_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = _fetcher(
        proxy_server="http://thehub.proxy-cheap.com:8080",
        proxy_username="user",
        proxy_password="secret",
        concurrency=3,
        request_delay=0,
    )

    async def fake_rotate(self: _ProxySession, reason: str) -> None:
        self.client = object()  # type: ignore[assignment]
        self.generation += 1
        self.requests_in_session = 0
        self.session_deadline = time.monotonic() + 3600

    monkeypatch.setattr(_ProxySession, "rotate", fake_rotate)

    async def run() -> None:
        await fetcher.__aenter__()
        try:
            assert len(fetcher._sessions) == 3
            first = await fetcher._checkout()
            second = await fetcher._checkout()
            third = await fetcher._checkout()
            assert {first.slot, second.slot, third.slot} == {0, 1, 2}
            # Pool exhausted until checkin.
            busy = asyncio.create_task(fetcher._checkout())
            await asyncio.sleep(0)
            assert not busy.done()
            fetcher._checkin(first)
            assert await asyncio.wait_for(busy, timeout=1.0) is first
            fetcher._checkin(second)
            fetcher._checkin(third)
        finally:
            await fetcher.__aexit__(None, None, None)

    asyncio.run(run())
