"""Wrestlers spider for thesmackdownhotel.com.

By default, discovers wrestler profile URLs from the server-rendered per-promotion
roster pages (`/roster/<slug>/`) for the configured promotions (WWE/AEW), which scopes
the crawl to the promotions we pair with Cagematch. The full XML sitemap (~2.2k
wrestlers) remains available via `discover_wrestler_urls()` for an unscoped run.
Each profile page is then parsed over plain HTTP.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from xml.etree import ElementTree

from parsel import Selector

from ...config import Settings
from ...http import fetch_text_sync
from ...items import (
    SdhWrestlerAlignmentEntry,
    SdhWrestlerCareerAwardEntry,
    SdhWrestlerHallOfFameEntry,
    SdhWrestlerImageEntry,
    SdhWrestlerItem,
    SdhWrestlerNameHistoryEntry,
    SdhWrestlerPromotionEntry,
    SdhWrestlerRoleEntry,
    SdhWrestlerTitleWinEntry,
)
from ..base import BaseSpider
from .utils import (
    BASE_URL,
    absolute_url,
    clean_text,
    field_value,
    nicknames_list,
    normalize_date,
    og_image,
    parse_born,
    parse_height_cm,
    parse_weight_kg,
    wrestler_slug_from_href,
)

logger = logging.getLogger(__name__)

SITEMAP_INDEX_URL = f"{BASE_URL}/sitemap-4seo.xml"
_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_WRESTLER_URL_RE = re.compile(
    r"^https?://(?:www\.)?thesmackdownhotel\.com/wrestlers/([^/#?]+)/?$"
)
_WRESTLER_HREF_RE = re.compile(r"^/wrestlers/([^/#?]+)/?$")
_TIMES_STRONG_RE = re.compile(r"^x(\d+)$", re.IGNORECASE)
_MANUAL_TITLE_RE = re.compile(
    r"^(?:(?P<times>\d+)\s+)?(?P<title>.+?)(?:\s*\((?P<details>[^)]*)\))?\s*$"
)


def discover_roster_urls(promotion_slugs: Iterable[str]) -> list[str]:
    """Collect wrestler profile URLs from each promotion's server-rendered all-time
    roster page (`/roster/?promotion=<slug>&date=all-time`), which includes alumni and
    legends — roughly 4x the coverage of the current-roster page at `/roster/<slug>/`.
    (robots.txt only disallows the day-specific `date=YYYY-MM-DD` roster variants.)
    """
    urls: list[str] = []
    seen: set[str] = set()
    for slug in promotion_slugs:
        roster_url = f"{BASE_URL}/roster/?promotion={slug}&date=all-time"
        try:
            html = fetch_text_sync(roster_url)
        except Exception:
            logger.exception("Failed to fetch SDH roster %s", roster_url)
            continue
        selector = Selector(text=html)
        for href in selector.css('a[href^="/wrestlers/"]::attr(href)').getall():
            match = _WRESTLER_HREF_RE.match(href.split("?")[0])
            if match is None or match.group(1) == "vacant":
                continue
            url = f"{BASE_URL}/wrestlers/{match.group(1)}"
            if url in seen:
                continue
            seen.add(url)
            urls.append(url)
    return urls


def discover_wrestler_urls() -> list[str]:
    """Walk the sitemap index and collect unique wrestler detail URLs."""
    try:
        index_xml = fetch_text_sync(SITEMAP_INDEX_URL)
    except Exception:
        logger.exception("Failed to fetch SDH sitemap index")
        return []

    urls: list[str] = []
    seen: set[str] = set()
    try:
        root = ElementTree.fromstring(index_xml)
    except ElementTree.ParseError:
        logger.exception("Failed to parse SDH sitemap index")
        return []

    shard_locs = [
        loc.text.strip()
        for loc in root.findall("sm:sitemap/sm:loc", _SITEMAP_NS)
        if loc.text and loc.text.strip()
    ]
    # Fallback if the document is served without the expected namespace binding.
    if not shard_locs:
        shard_locs = [
            loc.text.strip()
            for loc in root.iter()
            if loc.tag.endswith("loc") and loc.text and "sitemap" in (loc.text or "")
        ]

    for shard_url in shard_locs:
        try:
            shard_xml = fetch_text_sync(shard_url)
        except Exception:
            logger.exception("Failed to fetch SDH sitemap shard %s", shard_url)
            continue
        try:
            shard_root = ElementTree.fromstring(shard_xml)
        except ElementTree.ParseError:
            logger.exception("Failed to parse SDH sitemap shard %s", shard_url)
            continue
        for loc in shard_root.findall("sm:url/sm:loc", _SITEMAP_NS):
            text = (loc.text or "").strip()
            match = _WRESTLER_URL_RE.match(text)
            if match is None:
                continue
            # Skip non-profile utility pages if any.
            slug = match.group(1)
            if slug in {"vacant"}:
                continue
            if text in seen:
                continue
            seen.add(text)
            urls.append(text.rstrip("/"))

        # Namespace-less fallback
        if not any(True for _ in shard_root.findall("sm:url/sm:loc", _SITEMAP_NS)):
            for loc in shard_root.iter():
                if not loc.tag.endswith("loc") or not loc.text:
                    continue
                text = loc.text.strip()
                match = _WRESTLER_URL_RE.match(text)
                if match is None or match.group(1) == "vacant":
                    continue
                if text in seen:
                    continue
                seen.add(text)
                urls.append(text.rstrip("/"))

    return urls


def _dated_list_entries(
    selector: Selector, field_class: str
) -> list[tuple[str, str | None, str | None]]:
    """Parse `li.field-entry.<field>` lists of (value, from, to)."""
    entries: list[tuple[str, str | None, str | None]] = []
    container = selector.css(
        f"li.field-entry.{field_class} > .field-value > ul.fields-container > li"
    )
    for item in container:
        name = clean_text("".join(item.css(".field-entry.first .field-value ::text").getall()))
        if name is None:
            continue
        from_date = normalize_date(
            clean_text("".join(item.css(".field-entry.from-date .field-value ::text").getall()))
        )
        to_date = normalize_date(
            clean_text("".join(item.css(".field-entry.to-date .field-value ::text").getall()))
        )
        entries.append((name, from_date, to_date))
    return entries


def _parse_name_history(selector: Selector) -> list[SdhWrestlerNameHistoryEntry]:
    return [
        {"name": name, "from_date": from_date, "to_date": to_date}
        for name, from_date, to_date in _dated_list_entries(selector, "ring-names")
    ]


def _parse_roles(selector: Selector) -> list[SdhWrestlerRoleEntry]:
    return [
        {"role": name, "from_date": from_date, "to_date": to_date}
        for name, from_date, to_date in _dated_list_entries(selector, "roles")
    ]


def _parse_finishers(selector: Selector) -> list[str]:
    finishers: list[str] = []
    for name, _from, _to in _dated_list_entries(selector, "finishers"):
        finishers.append(name)
    return finishers


def _parse_promotions(selector: Selector) -> list[SdhWrestlerPromotionEntry]:
    entries: list[SdhWrestlerPromotionEntry] = []
    rows = selector.css("li.field-entry.companies table.fields-container tbody tr")
    for row in rows:
        promotion = clean_text(
            "".join(row.css("td .field-entry.promotion .field-value ::text").getall())
        )
        if promotion is None:
            continue
        brand = clean_text(
            "".join(row.css("td .field-entry.brands .field-value ::text").getall())
        )
        from_date = normalize_date(
            clean_text("".join(row.css(".field-entry.from-date .field-value ::text").getall()))
        )
        to_date = normalize_date(
            clean_text("".join(row.css(".field-entry.to-date .field-value ::text").getall()))
        )
        entries.append(
            {
                "promotion": promotion,
                "brand": brand,
                "from_date": from_date,
                "to_date": to_date,
            }
        )
    return entries


def _parse_alignments(selector: Selector) -> list[SdhWrestlerAlignmentEntry]:
    entries: list[SdhWrestlerAlignmentEntry] = []
    rows = selector.css("li.field-entry.alignments table.fields-container tbody tr")
    for row in rows:
        cells = row.css("td")
        if not cells:
            continue
        alignment = clean_text("".join(cells[0].css(".field-value ::text").getall()))
        if alignment is None:
            continue
        details = None
        if len(cells) > 1:
            details = clean_text("".join(cells[1].css(".field-value ::text").getall()))
        from_date = normalize_date(
            clean_text("".join(row.css(".field-entry.from-date .field-value ::text").getall()))
        )
        to_date = normalize_date(
            clean_text("".join(row.css(".field-entry.to-date .field-value ::text").getall()))
        )
        entries.append(
            {
                "alignment": alignment,
                "details": details,
                "from_date": from_date,
                "to_date": to_date,
            }
        )
    return entries


def _parse_images(selector: Selector) -> list[SdhWrestlerImageEntry]:
    """Dated headshot gallery ("Images History"). Only original `/images/wrestling/...`
    paths are guaranteed to exist at their stated URL; CDN-optimized variants
    (`/images/jch-optimize/...`) are stored as-is since the original path can't be
    reliably reconstructed (the optimizer rewrites the extension).
    """
    entries: list[SdhWrestlerImageEntry] = []
    for node in selector.css("li.field-entry.images .roster_section .roster"):
        src = node.css("img::attr(src)").get()
        url = absolute_url(src)
        if url is None:
            continue
        label = clean_text("".join(node.css(".roster_name ::text").getall()))
        entries.append({"label": label, "image_url": url})
    return entries


def _field_icon_url(node: Selector) -> str | None:
    return absolute_url(node.css("img.field-value-icon::attr(src)").get())


def _parse_career_awards(selector: Selector) -> list[SdhWrestlerCareerAwardEntry]:
    entries: list[SdhWrestlerCareerAwardEntry] = []
    for item in selector.css("li.field-entry.career-awards > .field-value > ul > li"):
        name = clean_text("".join(item.css("a span ::text").getall()))
        if name is None:
            name = clean_text("".join(item.css("a ::text").getall()))
        if name is None:
            continue
        entries.append(
            {
                "name": name,
                "url": absolute_url(item.css("a::attr(href)").get()),
                "image_url": _field_icon_url(item),
            }
        )
    return entries


def _parse_hall_of_fames(selector: Selector) -> list[SdhWrestlerHallOfFameEntry]:
    entries: list[SdhWrestlerHallOfFameEntry] = []
    rows = selector.css(
        "li.field-entry.hall-of-fames > .field-value > ul.fields-container > li"
    )
    for row in rows:
        name = clean_text(
            "".join(row.css(".field-entry.first .field-value ::text").getall())
        )
        if name is None:
            continue
        # Category is the non-first, non-year field-entry (e.g. "Individual").
        category = None
        for field in row.css(".field-entry"):
            classes = set(field.attrib.get("class", "").split())
            if "first" in classes or "year" in classes or "from-date" in classes:
                continue
            if "to-date" in classes:
                continue
            category = clean_text("".join(field.css(".field-value ::text").getall()))
            if category is not None:
                break
        year_text = clean_text(
            "".join(row.css(".field-entry.year .field-value ::text").getall())
        )
        year = int(year_text) if year_text and year_text.isdigit() else None
        entries.append(
            {
                "name": name,
                "category": category,
                "year": year,
                "url": absolute_url(row.css("a::attr(href)").get()),
                "image_url": _field_icon_url(row),
            }
        )
    return entries


def _parse_auto_title_wins(selector: Selector) -> list[SdhWrestlerTitleWinEntry]:
    entries: list[SdhWrestlerTitleWinEntry] = []
    container = selector.css("li.field-entry.titles-auto > .field-value")
    if not container:
        return entries
    # Children are alternating h3 + ul.unstyled groups.
    promotion: str | None = None
    for child in container.xpath("./*"):
        tag = (child.xpath("name()").get() or "").lower()
        if tag == "h3":
            promotion = clean_text("".join(child.css("::text").getall()))
            continue
        if tag != "ul" or promotion is None:
            continue
        for item in child.xpath("./li"):
            link = item.css("a")
            times = None
            strong = clean_text(link.css("strong::text").get())
            if strong is not None:
                match = _TIMES_STRONG_RE.match(strong)
                if match:
                    times = int(match.group(1))
            # Title text is link text minus the xN strong prefix; strip stray leading '/'.
            title_parts = [
                t.strip()
                for t in link.css("::text").getall()
                if t.strip() and _TIMES_STRONG_RE.match(t.strip()) is None
            ]
            title = clean_text(" ".join(title_parts))
            if title is not None:
                title = title.lstrip("/").strip() or None
            if title is None:
                continue
            details = clean_text("".join(item.css(".article-info ::text").getall()))
            entries.append(
                {
                    "promotion": promotion,
                    "title": title,
                    "times": times,
                    "details": details,
                    "title_url": absolute_url(link.css("::attr(href)").get()),
                    "image_url": _field_icon_url(item),
                    "source": "auto",
                }
            )
    return entries


def _parse_manual_title_wins(selector: Selector) -> list[SdhWrestlerTitleWinEntry]:
    entries: list[SdhWrestlerTitleWinEntry] = []
    container = selector.css("li.field-entry.titles > .field-value")
    if not container:
        return entries
    promotion: str | None = None
    for child in container.xpath("./*"):
        tag = (child.xpath("name()").get() or "").lower()
        if tag == "h3":
            promotion = clean_text("".join(child.css("::text").getall()))
            continue
        if tag != "ul" or promotion is None:
            continue
        for item in child.xpath("./li"):
            raw = clean_text("".join(item.css("::text").getall()))
            if raw is None:
                continue
            match = _MANUAL_TITLE_RE.match(raw)
            if match is None:
                continue
            times_raw = match.group("times")
            title = clean_text(match.group("title"))
            if title is None:
                continue
            details = clean_text(match.group("details"))
            if details is not None:
                details = f"({details})"
            entries.append(
                {
                    "promotion": promotion,
                    "title": title,
                    "times": int(times_raw) if times_raw else None,
                    "details": details,
                    "title_url": None,
                    "image_url": None,
                    "source": "manual",
                }
            )
    return entries


def _parse_title_wins(selector: Selector) -> list[SdhWrestlerTitleWinEntry]:
    return _parse_auto_title_wins(selector) + _parse_manual_title_wins(selector)


def _parse_accomplishments(selector: Selector) -> list[str]:
    values: list[str] = []
    for item in selector.css(
        "li.field-entry.accomplishments > .field-value > ul > li"
    ):
        text = clean_text("".join(item.css("::text").getall()))
        if text is not None:
            values.append(text)
    return values


def _current_weight_kg(selector: Selector) -> int | None:
    first = selector.css(
        "li.field-entry.weight > .field-value > ul.fields-container > li.present "
        ".field-entry.first .field-value ::text"
    ).getall()
    if not first:
        first = selector.css(
            "li.field-entry.weight > .field-value > ul.fields-container > li "
            ".field-entry.first .field-value ::text"
        ).getall()
    return parse_weight_kg(clean_text("".join(first)))


def _billed_from(selector: Selector) -> str | None:
    return clean_text(
        "".join(
            selector.css(
                "li.field-entry.billed-from > .field-value "
                ".field-entry.first .field-value ::text"
            ).getall()
        )
    ) or field_value(selector, "billed-from")


def parse_wrestler_page(selector: Selector, url: str) -> SdhWrestlerItem:
    slug = wrestler_slug_from_href(url)
    if slug is None:
        raise ValueError(f"Unrecognized SDH wrestler URL: {url}")

    birthday, age = parse_born(field_value(selector, "born"))
    name = clean_text(selector.css("h1::text").get()) or slug

    item: SdhWrestlerItem = {
        "id": slug,
        "name": name,
        "profile_url": url.split("?")[0].rstrip("/"),
        "real_name": field_value(selector, "real-name"),
        "gender": field_value(selector, "gender"),
        "birthday": birthday,
        "age": age,
        "nationality": field_value(selector, "nationality"),
        "birthplace": field_value(selector, "birth-place"),
        "billed_from": _billed_from(selector),
        "height_cm": parse_height_cm(field_value(selector, "height")),
        "weight_kg": _current_weight_kg(selector),
        "image_url": og_image(selector),
        "nicknames": nicknames_list(field_value(selector, "nicknames")),
        "finishers": _parse_finishers(selector),
        "name_history": _parse_name_history(selector),
        "promotions": _parse_promotions(selector),
        "roles": _parse_roles(selector),
        "alignments": _parse_alignments(selector),
        "images": _parse_images(selector),
        "career_awards": _parse_career_awards(selector),
        "hall_of_fames": _parse_hall_of_fames(selector),
        "title_wins": _parse_title_wins(selector),
        "accomplishments": _parse_accomplishments(selector),
    }
    return item


class SdhWrestlersSpider(BaseSpider):
    name = "sdh_wrestlers"
    fetch_backend = "http"
    fetch_profile = False

    def __init__(self, settings: Settings):
        self.settings = settings

    def start_requests(self) -> Iterable[str]:
        slugs = self.settings.sdh_promotion_slug_list()
        if slugs:
            return discover_roster_urls(slugs)
        # No promotion scoping configured: fall back to the full sitemap.
        return discover_wrestler_urls()

    def parse(self, selector: Selector, url: str) -> Iterable[dict]:
        yield parse_wrestler_page(selector, url)
