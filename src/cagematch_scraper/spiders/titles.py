"""Titles spider — stub.

TODO: cagematch.net's title database lives at `?id=9`, with title history pages
at `?id=9&nr=<title_id>`. Not implemented yet: needs live selector confirmation,
same as promotions.py.
"""

from __future__ import annotations

from collections.abc import Iterable

from parsel import Selector

from .base import BaseSpider


class TitlesSpider(BaseSpider):
    name = "titles"

    def start_requests(self) -> Iterable[str]:
        raise NotImplementedError(
            "titles spider not implemented yet; target: https://www.cagematch.net/?id=9"
        )

    def parse(self, selector: Selector, url: str) -> Iterable[dict]:
        raise NotImplementedError
