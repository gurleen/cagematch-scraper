"""Shared helpers for The Smackdown Hotel parsers."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from parsel import Selector

BASE_URL = "https://www.thesmackdownhotel.com"

_WS_RE = re.compile(r"\s+")
_HEIGHT_CM_RE = re.compile(r"\((\d+)\s*cm\)", re.IGNORECASE)
_WEIGHT_KG_RE = re.compile(r"\((\d+)\s*kg\)", re.IGNORECASE)
_BORN_RE = re.compile(
    r"^(?P<birthday>.+?)\s*\(age\s+(?P<age>\d+)\)\s*$", re.IGNORECASE
)
_DURATION_RE = re.compile(r"^(\d+)\+?$")


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = _WS_RE.sub(" ", value).strip()
    return text or None


def normalize_date(value: str | None) -> str | None:
    """Normalize SDH date strings; map Present/ongoing markers to None."""
    text = clean_text(value)
    if text is None:
        return None
    if text.casefold() in {"present", "current", "today", "ongoing"}:
        return None
    return text


def absolute_url(href: str | None) -> str | None:
    text = clean_text(href)
    if text is None:
        return None
    return urljoin(BASE_URL, text)


def og_image(selector: Selector) -> str | None:
    """The page's og:image — SDH serves the original (non-CDN-optimized) asset here."""
    return absolute_url(selector.css('meta[property="og:image"]::attr(content)').get())


def field_value(selector: Selector, field_class: str) -> str | None:
    """Read a top-level profile `li.field-entry.<field_class>` scalar value."""
    node = selector.css(f"li.field-entry.{field_class}")
    if not node:
        return None
    # Prefer text under the entry's own .field-value (nested subfields still match,
    # which is fine for scalar fields like real-name / gender / born).
    return clean_text(" ".join(node.css(".field-value ::text").getall()))


def parse_height_cm(raw: str | None) -> int | None:
    text = clean_text(raw)
    if text is None:
        return None
    match = _HEIGHT_CM_RE.search(text)
    return int(match.group(1)) if match else None


def parse_weight_kg(raw: str | None) -> int | None:
    text = clean_text(raw)
    if text is None:
        return None
    match = _WEIGHT_KG_RE.search(text)
    return int(match.group(1)) if match else None


def parse_born(raw: str | None) -> tuple[str | None, int | None]:
    text = clean_text(raw)
    if text is None:
        return None, None
    match = _BORN_RE.match(text)
    if match:
        return clean_text(match.group("birthday")), int(match.group("age"))
    return text, None


def parse_duration_days(raw: str | None) -> int | None:
    text = clean_text(raw)
    if text is None:
        return None
    match = _DURATION_RE.match(text)
    return int(match.group(1)) if match else None


def wrestler_slug_from_href(href: str | None) -> str | None:
    text = clean_text(href)
    if text is None:
        return None
    match = re.search(r"/wrestlers/([^/#?]+)", text)
    return match.group(1) if match else None


def title_id_from_url(url: str) -> str | None:
    match = re.search(r"/title-history/([^/]+)/([^/#?]+)/?$", url)
    if match is None:
        return None
    return f"{match.group(1)}/{match.group(2)}"


def nicknames_list(raw: str | None) -> list[str]:
    text = clean_text(raw)
    if text is None:
        return []
    return [part.strip() for part in text.split(";") if part.strip()]
