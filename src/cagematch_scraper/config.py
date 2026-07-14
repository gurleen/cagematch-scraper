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
    concurrency: int = 2
    request_delay: float = 1.5
    nav_timeout_ms: int = 30000
    output_dir: Path = Path("data")
    user_data_dir: Path | None = None
    block_resources: bool = True

    proxy_server: str | None = None
    proxy_username: str | None = None
    proxy_password: str | None = None
    proxy_bypass: str | None = None
    proxy_list_file: Path | None = Path("proxy-creds.txt")

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
