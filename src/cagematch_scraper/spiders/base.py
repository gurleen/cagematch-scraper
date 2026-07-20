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
    #: - "hybrid" (default): one patchright bootstrap to solve the Sucuri challenge,
    #:   then plain httpx reusing its cookie — what Cagematch spiders want.
    #: - "browser": every fetch through patchright (the pre-hybrid behavior; needed
    #:   when scraping through a rotating proxy pool, whose per-request exit IPs
    #:   invalidate the IP-bound Sucuri cookie the hybrid path depends on).
    #: - "http": plain httpx with no bootstrap, for SSR sites without a challenge.
    fetch_backend: Literal["browser", "http", "hybrid"] = "hybrid"

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

    def should_skip_resume(self, existing: dict, item: dict | None = None) -> bool:
        """Return True if an already-scraped item should not be re-fetched under
        `--resume`. Default keeps the original interrupt-recovery behavior: skip every
        id already present in the JSONL. Spiders that need to refresh stale rows
        (e.g. events scraped before results posted, active titles with new reigns)
        override this.

        `item` is the freshly parsed list/detail stub when available; spiders may use
        it (e.g. listing status) when the stored row alone is not enough.
        """
        return True
