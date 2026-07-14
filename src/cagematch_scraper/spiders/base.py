"""Spider base class: borrows Scrapy's mental model without the engine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from parsel import Selector


class BaseSpider(ABC):
    """Spiders are constructed as `spider_cls(settings)` (see `cli.py`)."""

    name: str

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
