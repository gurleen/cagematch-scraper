"""Runtime configuration, loaded from environment / .env (CAGEMATCH_ prefix)."""

from __future__ import annotations

import itertools
import random
import re
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_PROXY_LINE_RE = re.compile(
    r"^(?P<username>[^:@]+):(?P<password>[^:@]+)@(?P<host>[^:@]+):(?P<port>\d+)$"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CAGEMATCH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    base_url: str = "https://www.cagematch.net"
    headless: bool = True
    channel: str | None = "chromium"
    #: Max concurrent in-flight page fetches. With HybridFetcher + proxies this is
    #: also the sticky-session pool size: each slot gets its own exit IP, Sucuri
    #: cookie, and per-slot request-delay throttle so concurrent fetches do not
    #: share one residential IP. Without proxies the pool stays a single shared
    #: session. Kept modest by default — raise it when a proxy pool can absorb it.
    concurrency: int = 2
    #: Per-request start spacing is sampled uniformly from
    #: [`request_delay_min`, `request_delay_max`] seconds (see `next_request_delay`).
    request_delay_min: float = 0.8
    request_delay_max: float = 1.2
    nav_timeout_ms: int = 30000
    output_dir: Path = Path("data")
    user_data_dir: Path | None = None
    block_resources: bool = True

    #: Persistent DuckDB warehouse holding the flattened relational tables (see
    #: export/schema.sql) and the parquet files exported from it.
    warehouse_path: Path = Path("data/warehouse.duckdb")
    parquet_dir: Path = Path("data/parquet")
    export_cursor_path: Path = Path("data/.export_cursor.json")
    export_changes_path: Path = Path("data/.export_changes.json")

    #: Postgres connection string `export sync-postgres` mirrors the warehouse into
    #: (e.g. a Supabase session-pooler URL). Unset by default; the command errors
    #: clearly if it's needed but missing.
    postgres_url: str | None = None

    #: Comma-separated cagematch promotion ids to restrict promotion/wrestler/match
    #: scraping to. Default: WWE (1), AEW (2287). Empty string disables filtering
    #: (promotions spider only — wrestlers/matches always need at least one promotion,
    #: since both discover their data by walking a promotion's pages).
    promotion_ids: str = "1,2287"

    #: Earliest year (inclusive) the matches spider fetches events for.
    matches_since_year: int = 2020

    #: Under `--resume`, re-fetch events whose date is within this many days of today
    #: (including near-future dates) even if they already have match results — late
    #: ratings and card corrections still land. Incomplete older events (empty/missing
    #: `matches`) are always refreshed so a pre-air scrape gets results later. Default
    #: is 1 (today and yesterday) so nightly does not re-pull weeks of complete cards.
    matches_refresh_days: int = 1

    #: Under `--resume`, events more than this many days in the future are only
    #: re-fetched when `event_type` is a PPV/PLE (`Pay Per View` / `Premium Live
    #: Event`). TV shows, house shows, etc. that far out are left alone until they
    #: fall inside this window.
    matches_far_future_days: int = 30

    #: Comma-separated The Smackdown Hotel promotion slugs the SDH spiders restrict to.
    #: Titles are discovered from each slug's `/title-history/<slug>/` index and
    #: wrestlers from its `/roster/<slug>/` page (both server-rendered). Default:
    #: WWE + AEW, mirroring `promotion_ids`.
    sdh_promotion_slugs: str = "wwe,aew"

    def promotion_id_list(self) -> list[str] | None:
        ids = [x.strip() for x in self.promotion_ids.split(",") if x.strip()]
        return ids or None

    def sdh_promotion_slug_list(self) -> list[str]:
        return [s.strip() for s in self.sdh_promotion_slugs.split(",") if s.strip()]

    def next_request_delay(self) -> float:
        """Seconds to wait before the next request start on a throttle lane."""
        low = min(self.request_delay_min, self.request_delay_max)
        high = max(self.request_delay_min, self.request_delay_max)
        if high <= 0:
            return 0.0
        return random.uniform(low, high)

    proxy_server: str | None = None
    proxy_username: str | None = None
    proxy_password: str | None = None
    proxy_bypass: str | None = None
    proxy_list_file: Path | None = Path("proxy-creds.txt")

    #: Proxy-Cheap rotating-residential sticky-session settings used by HybridFetcher.
    #: It appends `_session-<random>_ttl-<minutes>` to a pool credential's password,
    #: then rotates to a fresh session/IP after this many HTTP requests (or before the
    #: provider TTL expires), solving the Sucuri challenge again on the new IP.
    proxy_session_max_requests: int = 100
    proxy_session_ttl_minutes: int = 10

    #: Legacy fallback for HybridFetcher when no proxy pool is configured.
    static_proxy: str | None = None

    def proxy_dict(self) -> dict[str, str] | None:
        if not self.proxy_server:
            return None
        proxy: dict[str, str] = {"server": self.proxy_server}
        if self.proxy_username:
            proxy["username"] = self.proxy_username
        if self.proxy_password:
            proxy["password"] = self.proxy_password
        if self.proxy_bypass:
            proxy["bypass"] = self.proxy_bypass
        return proxy

    def load_proxy_pool(self) -> list[dict[str, str]]:
        """Parse `proxy_list_file` (one `USER:PASS@HOST:PORT` per line) into playwright proxy dicts."""
        if self.proxy_list_file is None or not self.proxy_list_file.exists():
            return []
        proxies: list[dict[str, str]] = []
        for raw_line in self.proxy_list_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = _PROXY_LINE_RE.match(line)
            if not match:
                continue
            proxies.append(
                {
                    "server": f"http://{match.group('host')}:{match.group('port')}",
                    "username": match.group("username"),
                    "password": match.group("password"),
                }
            )
        return proxies

    def load_static_proxy(self) -> dict[str, str] | None:
        """Parse `static_proxy` (`USER:PASS@HOST:PORT`) into a playwright-style proxy
        dict. Returns None when unset; raises on a malformed value rather than silently
        falling back to a direct connection.
        """
        if not self.static_proxy:
            return None
        match = _PROXY_LINE_RE.match(self.static_proxy.strip())
        if not match:
            raise ValueError(
                "CAGEMATCH_STATIC_PROXY must look like USER:PASS@HOST:PORT, "
                f"got {self.static_proxy!r}"
            )
        return {
            "server": f"http://{match.group('host')}:{match.group('port')}",
            "username": match.group("username"),
            "password": match.group("password"),
        }


class ProxyPool:
    """Cycles through a list of proxy dicts, deduplicated by (server, username)."""

    def __init__(self, proxies: list[dict[str, str]]):
        deduped: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for proxy in proxies:
            key = (proxy["server"], proxy.get("username", ""))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(proxy)
        self._proxies = deduped
        self._cycle = itertools.cycle(deduped) if deduped else None

    def __len__(self) -> int:
        return len(self._proxies)

    def __iter__(self):
        return iter(self._proxies)

    def __bool__(self) -> bool:
        return self._cycle is not None

    def next(self) -> dict[str, str] | None:
        if self._cycle is None:
            return None
        return next(self._cycle)
