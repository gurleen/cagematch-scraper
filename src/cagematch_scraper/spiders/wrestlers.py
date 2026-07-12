"""Wrestlers spider — stub.

TODO: cagematch.net's wrestler database lives at `?id=2`, with profile pages at
`?id=2&nr=<wrestler_id>`, mirroring the promotions section (see promotions.py).
Not implemented yet: this needs the same live selector confirmation that
promotions.py is still waiting on.
"""

from __future__ import annotations

from collections.abc import Iterable

from parsel import Selector

from .base import BaseSpider


class WrestlersSpider(BaseSpider):
    name = "wrestlers"

    def start_requests(self) -> Iterable[str]:
        raise NotImplementedError(
            "wrestlers spider not implemented yet; target: https://www.cagematch.net/?id=2"
        )

    def parse(self, selector: Selector, url: str) -> Iterable[dict]:
        raise NotImplementedError
