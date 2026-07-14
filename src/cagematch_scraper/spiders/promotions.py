"""Promotions spider — cagematch.net's promotion database (id=8, view=promotions).

Confirmed live against cagematch.net: the browsable list lives at
`?id=8&view=promotions`, paginated via `s=<row offset>` in steps of 100 (not a
1-based page number). Each row is a `<tr>` with cells
`[rank, logo, name, location, active_years, rating, votes]`.

Also confirmed live: each promotion's profile page (`?id=8&nr=<id>`) has a "Names:"
info box listing every name the promotion has used, one per line as
`<name> (<from date> - <to date|today>)`, e.g. WWE's includes "World Wrestling
Federation (30.03.1979 - 04.05.2002)". `parse_profile` extracts that into
`name_history`; it's only fetched when `fetch_profile` is enabled, since it costs
one extra request per promotion.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from parsel import Selector

from ..items import PromotionItem, PromotionNameHistoryEntry
from .base import BaseSpider

SECTION_ID = 8
PAGE_SIZE = 100
LIST_URL = "https://www.cagematch.net/?id={section}&view=promotions&s={offset}"
PROFILE_LINK_RE = re.compile(rf"[?&]id={SECTION_ID}&nr=(\d+)")
NAME_HISTORY_ENTRY_RE = re.compile(r"^(?P<name>.*?)\s*\((?P<from>.*?)\s*-\s*(?P<to>.*?)\)$")


def _parse_active_years(text: str) -> tuple[int | None, int | None]:
    text = text.strip()
    if not text:
        return None, None
    if "-" in text:
        start_s, _, end_s = text.partition("-")
        start = int(start_s) if start_s.strip().isdigit() else None
        end = int(end_s) if end_s.strip().isdigit() else None
        return start, end
    if text.isdigit():
        year = int(text)
        return year, year
    return None, None


def _parse_rating(text: str) -> float | None:
    try:
        return float(text.strip())
    except ValueError:
        return None


def _parse_votes(text: str) -> int | None:
    try:
        return int(text.strip())
    except ValueError:
        return None


def _parse_name_history(selector: Selector) -> list[PromotionNameHistoryEntry]:
    for title in selector.css("div.InformationBoxTitle"):
        label = "".join(title.css("::text").getall()).strip()
        if label != "Names:":
            continue

        content = title.xpath("following-sibling::div[1]")
        inner_html = content.get() or ""
        inner_html = re.sub(r"^<div[^>]*>|</div>$", "", inner_html.strip())

        entries: list[PromotionNameHistoryEntry] = []
        for fragment in re.split(r"<br\s*/?>", inner_html):
            text = " ".join(Selector(text=fragment).css("::text").getall()).strip()
            if not text:
                continue
            match = NAME_HISTORY_ENTRY_RE.match(text)
            if not match:
                continue
            to_date = match.group("to").strip()
            entries.append(
                {
                    "name": match.group("name").strip(),
                    "from_date": match.group("from").strip() or None,
                    "to_date": None if to_date.lower() == "today" else to_date or None,
                }
            )
        return entries
    return []


class PromotionsSpider(BaseSpider):
    name = "promotions"
    fetch_profile = True

    def __init__(self, max_pages: int = 1):
        self.max_pages = max_pages

    def start_requests(self) -> Iterable[str]:
        for page in range(self.max_pages):
            yield LIST_URL.format(section=SECTION_ID, offset=page * PAGE_SIZE)

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

            name = "".join(link.css("::text").getall()).strip()
            if not name:
                continue
            seen.add(promotion_id)

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
            if len(cell_texts) > 3:
                item["location"] = cell_texts[3]
            if len(cell_texts) > 4:
                item["active_year_start"], item["active_year_end"] = _parse_active_years(
                    cell_texts[4]
                )
            if len(cell_texts) > 5:
                item["rating"] = _parse_rating(cell_texts[5])
            if len(cell_texts) > 6:
                item["votes"] = _parse_votes(cell_texts[6])
            yield item

    def parse_profile(self, selector: Selector, item: dict) -> dict:
        item["name_history"] = _parse_name_history(selector)
        return item
