"""Runtime configuration, loaded from environment / .env (CAGEMATCH_ prefix)."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    proxy_server: str | None = None
    proxy_username: str | None = None
    proxy_password: str | None = None
    proxy_bypass: str | None = None

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
