"""Runtime configuration, loaded from environment / .env (CAGEMATCH_ prefix)."""

from __future__ import annotations

import itertools
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
    #: Max concurrent in-flight page fetches. Each still waits its turn behind
    #: `request_delay` (see `BrowserManager._throttle`), but with concurrency > 1
    #: multiple fetches can be in network-wait at once instead of one at a time.
    #: Kept modest by default — some proxies cap concurrent tunnel connections
    #: (concurrency=4 hit `ERR_TUNNEL_CONNECTION_FAILED` against the configured proxy
    #: in testing; concurrency=2 was stable). Raise it if your proxy can take it.
    concurrency: int = 2
    request_delay: float = 1.5
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
