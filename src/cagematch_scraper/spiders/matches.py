"""Matches spider — stub.

TODO: match/event data on cagematch.net lives under the events database
(`?id=1`) with individual event pages linking to match cards. Not implemented
yet: needs live selector confirmation, same as promotions.py.
"""

from __future__ import annotations

from collections.abc import Iterable

from parsel import Selector

from ..config import Settings
from .base import BaseSpider


class MatchesSpider(BaseSpider):
    name = "matches"

    def __init__(self, settings: Settings):
        self.settings = settings

    def start_requests(self) -> Iterable[str]:
        raise NotImplementedError(
            "matches spider not implemented yet; target: https://www.cagematch.net/?id=1"
        )

    def parse(self, selector: Selector, url: str) -> Iterable[dict]:
        raise NotImplementedError
