"""Promotions spider — cagematch.net's promotion database (id=8).

NOTE: the exact section id (8) and CSS selectors below are based on cagematch.net's
known URL/markup conventions (`?id=8` promotion database, `nr=<id>` detail links,
`TBase`/`TRow1`/`TRow2` table classes used site-wide). They have NOT been confirmed
by driving a live browser against the site from this environment, because this
sandbox's headless Chromium cannot complete a TLS handshake through the mandatory
egress proxy (see README "Known limitations"). Parsing intentionally falls back to
generic `nr=` link matching rather than a single hard-coded table class, so it stays
resilient if the exact class names are slightly off — but treat the field extraction
as best-effort until it's been run against the live site and adjusted.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from parsel import Selector

from ..items import PromotionItem
from .base import BaseSpider

SECTION_ID = 8
LIST_URL = "https://www.cagematch.net/?id={section}&page=4"
PROFILE_LINK_RE = re.compile(rf"[?&]id={SECTION_ID}&nr=(\d+)")


class PromotionsSpider(BaseSpider):
    name = "promotions"

    def __init__(self, max_pages: int = 1):
        self.max_pages = max_pages

    def start_requests(self) -> Iterable[str]:
        for page in range(1, self.max_pages + 1):
            yield f"{LIST_URL.format(section=SECTION_ID)}&s={page}"

    def parse(self, selector: Selector, url: str) -> Iterable[PromotionItem]:
        seen: set[str] = set()
        for link in selector.css("a"):
            href = link.attrib.get("href", "")
            match = PROFILE_LINK_RE.search(href)
            if not match:
                continue
            promotion_id = match.group(1)
            if promotion_id in seen:
                continue
            seen.add(promotion_id)

            name = "".join(link.css("::text").getall()).strip()
            if not name:
                continue

            row = link.xpath("./ancestor::tr[1]")
            cell_texts = [
                " ".join(cell.css("::text").getall()).strip()
                for cell in (row.css("td") if row else [])
            ]

            item: PromotionItem = {
                "id": promotion_id,
                "name": name,
                "profile_url": f"https://www.cagematch.net/?id={SECTION_ID}&nr={promotion_id}",
            }
            if len(cell_texts) > 1:
                item["location"] = cell_texts[1]
            if len(cell_texts) > 2:
                item["status"] = cell_texts[2]
            yield item
