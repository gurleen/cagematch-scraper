"""Titles spider for thesmackdownhotel.com.

Discovers titles from SSR promotion indexes (`/title-history/{promo}/`), then parses
each title detail page for metadata and the full reign table. Uses plain HTTP — pages
are server-rendered.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from parsel import Selector

from ...config import Settings
from ...http import fetch_text_sync
from ...items import (
    SdhTitleItem,
    SdhTitleNameHistoryEntry,
    SdhTitleReign,
    SdhTitleReignChampion,
)
from ..base import BaseSpider
from .utils import (
    BASE_URL,
    absolute_url,
    clean_text,
    field_value,
    normalize_date,
    og_image,
    parse_duration_days,
    title_id_from_url,
    wrestler_slug_from_href,
)

logger = logging.getLogger(__name__)

# Promotion indexes that are server-rendered (unlike the JS MegaFilter master index).
PROMOTION_SLUGS = ("wwe", "aew", "tna", "njpw", "roh", "nwa", "wcw", "ecw")

TITLE_HREF_RE = re.compile(r"^/title-history/([^/]+)/([^/#?]+)/?$")


def discover_title_urls(promotion_slugs: Iterable[str] = PROMOTION_SLUGS) -> list[str]:
    """Fetch each promotion index and collect unique title detail URLs."""
    urls: list[str] = []
    seen: set[str] = set()
    for slug in promotion_slugs:
        index_url = f"{BASE_URL}/title-history/{slug}/"
        try:
            html = fetch_text_sync(index_url)
        except Exception:
            logger.exception("Failed to fetch SDH title index %s", index_url)
            continue
        selector = Selector(text=html)
        for href in selector.css('a[href*="/title-history/"]::attr(href)').getall():
            match = TITLE_HREF_RE.match(href.split("?")[0])
            if match is None or match.group(1) != slug:
                continue
            url = absolute_url(match.group(0))
            if url is None or url in seen:
                continue
            seen.add(url)
            urls.append(url)
    return urls


def _parse_reign_row(row: Selector) -> SdhTitleReign:
    reign_number_raw = clean_text(row.css("td.reign-number::text").get())
    reign_number = int(reign_number_raw) if reign_number_raw and reign_number_raw.isdigit() else None

    heading = row.css("td.reign-info h3.contentheading")
    champion_links = heading.css("a[href*='/wrestlers/']")
    champions: list[SdhTitleReignChampion] = []
    is_vacant = False

    badge_raw = clean_text(heading.css("span.badge::text").get())
    badge_count = int(badge_raw) if badge_raw and badge_raw.isdigit() else None

    for link in champion_links:
        href = link.attrib.get("href")
        slug = wrestler_slug_from_href(href)
        name = clean_text("".join(link.css("::text").getall()))
        if slug == "vacant" or (name and name.casefold() == "vacant"):
            is_vacant = True
            continue
        if slug is None or name is None:
            continue
        champ: SdhTitleReignChampion = {"id": slug, "name": name}
        if badge_count is not None and len(champion_links) == 1:
            champ["title_reign_count"] = badge_count
        champions.append(champ)

    if not champion_links:
        heading_text = clean_text("".join(heading.css("::text").getall()))
        if heading_text and heading_text.casefold() == "vacant":
            is_vacant = True

    if is_vacant:
        champions = []

    days_raw = clean_text(row.css("td.days-held div::text").get())
    event_href = row.css("td.reign-info .event a::attr(href)").get()
    event_name = clean_text("".join(row.css("td.reign-info .event a::text").getall()))
    notes = clean_text("".join(row.css("td.reign-info .notes ::text").getall()))

    reign: SdhTitleReign = {
        "reign_number": reign_number,
        "champions": champions,
        "from_date": normalize_date(
            clean_text("".join(row.css("td.reign-info .from-date ::text").getall()))
        ),
        "to_date": None,
        "duration_days": parse_duration_days(days_raw),
        "location": clean_text("".join(row.css("td.reign-info .location ::text").getall())),
        "event_name": event_name,
        "event_url": absolute_url(event_href),
        "notes": notes,
        "is_vacant": is_vacant,
    }
    return reign


def _fill_to_dates(reigns: list[SdhTitleReign]) -> None:
    """SDH shows start dates only; end date is the next (newer) reign's start.

    Reign rows are newest-first on the page, so the previous row's from_date is this
    reign's end — except the newest reign, which stays open (to_date None).
    """
    for index, reign in enumerate(reigns):
        if index == 0:
            continue
        # reigns[index] is older than reigns[index - 1]
        newer_start = reigns[index - 1].get("from_date")
        if newer_start:
            reign["to_date"] = newer_start


def _parse_name_history(selector: Selector) -> list[SdhTitleNameHistoryEntry]:
    entries: list[SdhTitleNameHistoryEntry] = []
    for item in selector.css("li.field-entry.title-variants li"):
        name = clean_text("".join(item.css(".field-entry.name .field-value ::text").getall()))
        if name is None:
            continue
        from_date = normalize_date(
            clean_text("".join(item.css(".field-entry.from-date .field-value ::text").getall()))
        )
        to_date = normalize_date(
            clean_text("".join(item.css(".field-entry.to-date .field-value ::text").getall()))
        )
        # The lightbox anchor's href is the original full-size belt image; the <img>
        # src is a CDN-optimized (resized/avif) variant, so prefer the anchor.
        image_href = item.css(".field-entry.picture a[data-modals]::attr(href)").get()
        if image_href is None:
            image_href = item.css(".field-entry.picture img::attr(src)").get()
        entries.append(
            {
                "name": name,
                "from_date": from_date,
                "to_date": to_date,
                "image_url": absolute_url(image_href),
            }
        )
    return entries


def parse_title_page(selector: Selector, url: str) -> SdhTitleItem:
    title_id = title_id_from_url(url)
    if title_id is None:
        raise ValueError(f"Unrecognized SDH title URL: {url}")

    name = clean_text(selector.css("h1::text").get()) or title_id.split("/")[-1]
    reigns = [_parse_reign_row(row) for row in selector.css("table.title-reigns tr.title-reign")]
    _fill_to_dates(reigns)

    item: SdhTitleItem = {
        "id": title_id,
        "name": name,
        "profile_url": url.split("?")[0],
        "promotion": field_value(selector, "promotions") or field_value(selector, "promotion"),
        "brand": field_value(selector, "brand"),
        "gender": field_value(selector, "gender"),
        "date_established": field_value(selector, "date-established"),
        "current_champion": None,
        "territory": field_value(selector, "territory"),
        "title_type": field_value(selector, "title-type"),
        "image_url": og_image(selector),
        "name_history": _parse_name_history(selector),
        "reigns": reigns,
    }

    # "Current Champion" is often a bare field-entry without a specific class.
    for entry in selector.css("li.field-entry"):
        label = clean_text("".join(entry.css(".field-label ::text").getall()))
        if label and label.casefold() == "current champion":
            item["current_champion"] = clean_text(
                "".join(entry.css(".field-value ::text").getall())
            )
            break

    return item


class SdhTitlesSpider(BaseSpider):
    name = "sdh_titles"
    fetch_backend = "http"
    fetch_profile = False

    def __init__(self, settings: Settings):
        self.settings = settings

    def start_requests(self) -> Iterable[str]:
        return discover_title_urls(self.settings.sdh_promotion_slug_list())

    def parse(self, selector: Selector, url: str) -> Iterable[dict]:
        yield parse_title_page(selector, url)
