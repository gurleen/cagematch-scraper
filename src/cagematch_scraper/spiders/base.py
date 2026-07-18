"""Spider base class: borrows Scrapy's mental model without the engine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Literal

from parsel import Selector


class BaseSpider(ABC):
    """Spiders are constructed as `spider_cls(settings)` (see `cli.py`)."""

    name: str

    #: Which transport the runner should use for this spider's fetches.
    #: Cagematch needs a real browser (Sucuri); SSR sites can use plain HTTP.
    fetch_backend: Literal["browser", "http"] = "browser"

    #: If True, the runner fetches each yielded item's `profile_url` (when present)
    #: and calls `parse_profile` to enrich it before writing. Costs one extra
    #: request per item, so spiders opt in deliberately.
    fetch_profile: bool = False

    @abstractmethod
    def start_requests(self) -> Iterable[str]:
        """Yield the URLs to fetch first."""

    @abstractmethod
    def parse(self, selector: Selector, url: str) -> Iterable[dict]:
        """Parse a fetched page into item dicts."""

    def parse_profile(self, selector: Selector, item: dict) -> dict:
        """Optionally enrich `item` using its profile page. Default: no-op."""
        return item

    def next_page_url(self, selector: Selector, url: str) -> str | None:
        """Optionally return a follow-up URL to fetch after `url` (e.g. the next page
        of a listing whose total page count isn't known upfront). Called by the runner
        after `parse` on every list-page fetch (not on profile-page fetches). Default:
        no follow-up — use when a spider's `start_requests` already enumerates every
        page it needs (as `promotions.py`/`wrestlers.py` do).
        """
        return None
