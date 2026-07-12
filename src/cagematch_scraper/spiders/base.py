"""Spider base class: borrows Scrapy's mental model without the engine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from parsel import Selector


class BaseSpider(ABC):
    name: str

    @abstractmethod
    def start_requests(self) -> Iterable[str]:
        """Yield the URLs to fetch first."""

    @abstractmethod
    def parse(self, selector: Selector, url: str) -> Iterable[dict]:
        """Parse a fetched page into item dicts."""
