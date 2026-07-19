import asyncio
import time

import pytest

from cagematch_scraper import hybrid
from cagematch_scraper.config import Settings
from cagematch_scraper.hybrid import HybridFetcher, _with_sticky_session


def _fetcher() -> HybridFetcher:
    settings = Settings(_env_file=None, proxy_list_file=None)
    return HybridFetcher(settings)


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


def test_request_budget_rotates_before_reserving_next_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        proxy_server="http://thehub.proxy-cheap.com:8080",
        proxy_username="user",
        proxy_password="secret",
        proxy_session_max_requests=2,
    )
    fetcher = HybridFetcher(settings)
    fetcher._client = object()  # type: ignore[assignment]
    fetcher._session_generation = 1
    fetcher._requests_in_session = 2

    async def fake_bootstrap() -> None:
        fetcher._session_generation += 1
        fetcher._requests_in_session = 0

    monkeypatch.setattr(fetcher, "_bootstrap", fake_bootstrap)

    async def run() -> None:
        _, generation = await fetcher._reserve_request()
        assert generation == 2
        assert fetcher._requests_in_session == 1
        await fetcher._release_request()

    asyncio.run(run())


def test_failed_rebootstrap_sets_growing_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hybrid, "_BOOTSTRAP_COOLDOWN_BASE_SECONDS", 0.01)
    fetcher = _fetcher()

    async def failing_bootstrap() -> None:
        raise RuntimeError("challenge did not clear")

    monkeypatch.setattr(fetcher, "_bootstrap", failing_bootstrap)

    async def run() -> None:
        for expected_failures in (1, 2, 3):
            with pytest.raises(RuntimeError):
                await fetcher._rebootstrap(fetcher._session_generation)
            assert fetcher._bootstrap_failures == expected_failures
            assert fetcher._cooldown_until > time.monotonic() - 1

    asyncio.run(run())
    # Exponential: third failure's cooldown is 4x base.
    assert fetcher._cooldown_until - time.monotonic() <= 0.04 + 0.01


def test_successful_rebootstrap_resets_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hybrid, "_BOOTSTRAP_COOLDOWN_BASE_SECONDS", 0.01)
    fetcher = _fetcher()
    fetcher._bootstrap_failures = 2
    fetcher._cooldown_until = time.monotonic() + 0.01

    async def ok_bootstrap() -> None:
        fetcher._session_generation += 1

    monkeypatch.setattr(fetcher, "_bootstrap", ok_bootstrap)
    asyncio.run(fetcher._rebootstrap(fetcher._session_generation))

    assert fetcher._bootstrap_failures == 0
    assert fetcher._cooldown_until == 0.0


def test_rebootstrap_skipped_when_generation_moved(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = _fetcher()

    async def unexpected_bootstrap() -> None:
        raise AssertionError("bootstrap should not run for a stale generation")

    monkeypatch.setattr(fetcher, "_bootstrap", unexpected_bootstrap)
    fetcher._session_generation = 5
    asyncio.run(fetcher._rebootstrap(4))
