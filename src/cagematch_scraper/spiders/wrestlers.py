"""Wrestlers spider — cagematch.net's wrestler database (id=2).

Confirmed live against cagematch.net: there's no bare "browse all wrestlers" list, so
this spider discovers wrestlers via two tabs on each configured promotion's page:

- Roster (`?id=8&nr=<promotion_id>&page=15`): cagematch's curated affiliation list.
  Broader than "currently active" (it includes retired legends like Ted DiBiase), but
  not a full history either. Table columns: `[#, gimmick, roles, (brand,) rating,
  votes]` — the brand column only exists for promotions that split into brands (WWE
  does, AEW doesn't), so rating/votes are always read from the last two cells rather
  than a fixed index.
- All-Time Roster (`?id=8&nr=<promotion_id>&page=16`): a separate, appearance-count-based
  list. Largely non-overlapping with Roster — confirmed live for WWE, it's missing some
  top legends that Roster has, but includes ~113 wrestlers Roster doesn't (recent
  departures, lower-card names). Table columns: `[#, gimmick, # shows]`. Also lists
  tag-team/stable entries, but those link to `id=28`/`id=29` (teams/stables), not
  `id=2` (wrestlers), so they're naturally excluded by the `id=2&nr=` link match.

Fetching both and deduping by wrestler id (Roster processed first, so its richer
fields win for wrestlers present in both) gets closer to "everyone who was ever on the
promotion's roster" than either tab alone.

Each wrestler's own profile page (`?id=2&nr=<wrestler_id>`) is then fetched (via
`fetch_profile`/`parse_profile`) to pull as much career and personal data as cagematch
publishes: birthday/birthplace/gender/height/weight/background, alter egos, nicknames,
signature moves, wrestling style, trainers, in-ring career span/experience, and a full
role history with date ranges (a role can have multiple non-contiguous ranges, e.g.
"Ring Announcer (1999 - 2009, 2011 - 2016, 2024 - today)").

Restricted by `Settings.promotion_ids` (default: WWE + AEW) — this spider requires at
least one promotion id, since that's how it finds wrestlers to begin with.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from parsel import Selector

from ..config import Settings
from ..items import WrestlerDateRange, WrestlerItem, WrestlerRoleEntry
from .base import BaseSpider
from .htmlutils import br_list, info_boxes as _info_boxes, text_of as _text_of
from .promotions import _parse_rating, _parse_votes

ROSTER_URL = "https://www.cagematch.net/?id=8&nr={promotion_id}&page=15"
ALL_TIME_ROSTER_URL = "https://www.cagematch.net/?id=8&nr={promotion_id}&page=16"
PROFILE_URL = "https://www.cagematch.net/?id=2&nr={wrestler_id}"
ROSTER_LINK_RE = re.compile(r"[?&]id=2&nr=(\d+)")
ROLE_ENTRY_RE = re.compile(r"^(?P<role>.*?)(?:\s*\((?P<ranges>.+)\))?$")


def _int_prefix(text: str) -> int | None:
    match = re.match(r"\s*(\d+)", text)
    return int(match.group(1)) if match else None


def _parse_height_cm(text: str) -> int | None:
    match = re.search(r"\((\d+)\s*cm\)", text)
    return int(match.group(1)) if match else None


def _parse_weight_kg(text: str) -> int | None:
    match = re.search(r"\((\d+)\s*kg\)", text)
    return int(match.group(1)) if match else None


def _comma_list(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def _br_list(content: Selector) -> list[str]:
    return br_list(content.get() or "")


def _link_texts(content: Selector) -> list[str]:
    texts: list[str] = []
    for raw in content.css("a::text").getall():
        text = raw.strip()
        if text and text not in texts:
            texts.append(text)
    return texts


def _split_date_range(chunk: str) -> WrestlerDateRange:
    chunk = chunk.strip()
    if " - " in chunk:
        from_date, _, to_date = chunk.partition(" - ")
    else:
        from_date = to_date = chunk
    from_date = from_date.strip()
    to_date = to_date.strip()
    return {
        "from_date": from_date or None,
        "to_date": None if to_date.lower() == "today" else (to_date or None),
    }


def _parse_role_entries(content: Selector) -> list[WrestlerRoleEntry]:
    entries: list[WrestlerRoleEntry] = []
    for text in _br_list(content):
        match = ROLE_ENTRY_RE.match(text)
        if not match:
            continue
        ranges_raw = match.group("ranges")
        date_ranges = (
            [_split_date_range(chunk) for chunk in ranges_raw.split(",")] if ranges_raw else []
        )
        entries.append({"role": match.group("role").strip(), "date_ranges": date_ranges})
    return entries


class WrestlersSpider(BaseSpider):
    name = "wrestlers"
    fetch_profile = True

    def __init__(self, settings: Settings):
        promotion_ids = settings.promotion_id_list()
        if not promotion_ids:
            raise ValueError(
                "wrestlers spider requires at least one promotion id "
                "(set CAGEMATCH_PROMOTION_IDS)"
            )
        self.promotion_ids = promotion_ids
        self._seen: set[str] = set()

    def start_requests(self) -> Iterable[str]:
        # Roster before All-Time Roster, per promotion: a wrestler present in both is
        # deduped to its first sighting, and Roster's fields (roles/brand/rating) are
        # richer than All-Time Roster's (just a show count).
        for promotion_id in self.promotion_ids:
            yield ROSTER_URL.format(promotion_id=promotion_id)
        for promotion_id in self.promotion_ids:
            yield ALL_TIME_ROSTER_URL.format(promotion_id=promotion_id)

    def parse(self, selector: Selector, url: str) -> Iterable[WrestlerItem]:
        promo_match = re.search(r"[?&]nr=(\d+)", url)
        promotion_id = promo_match.group(1) if promo_match else None
        is_all_time = "page=16" in url

        tables = selector.css("table.TBase")
        rows = tables[0].css("tr")[1:] if tables else []
        for row in rows:
            links = row.css("a")
            if not links:
                continue
            link = links[0]
            match = ROSTER_LINK_RE.search(link.attrib.get("href", ""))
            if not match:
                continue
            wrestler_id = match.group(1)
            if wrestler_id in self._seen:
                continue
            self._seen.add(wrestler_id)

            name = "".join(link.css("::text").getall()).strip()
            if not name:
                continue

            cells = [" ".join(c.css("::text").getall()).strip() for c in row.css("td")]

            item: WrestlerItem = {
                "id": wrestler_id,
                "name": name,
                "profile_url": PROFILE_URL.format(wrestler_id=wrestler_id),
                "promotions": [promotion_id] if promotion_id else [],
            }
            if is_all_time:
                # Columns: [#, gimmick, # shows] — no roles/brand/rating here.
                if len(cells) > 2:
                    item["career_shows"] = _parse_votes(cells[2])
            else:
                if len(cells) > 2:
                    item["active_roles"] = _comma_list(cells[2])
                if len(cells) == 6:
                    item["current_brand"] = cells[3]
                if len(cells) >= 4:
                    item["roster_rating"] = _parse_rating(cells[-2])
                    item["roster_votes"] = _parse_votes(cells[-1])
            yield item

    def parse_profile(self, selector: Selector, item: dict) -> dict:
        boxes = _info_boxes(selector)

        if "Age:" in boxes:
            item["age"] = _int_prefix(_text_of(boxes["Age:"]))
        if "Promotion:" in boxes:
            item["current_promotion"] = _text_of(boxes["Promotion:"])
        if "Brand:" in boxes:
            item["current_brand"] = _text_of(boxes["Brand:"])
        if "Active Roles:" in boxes:
            item["active_roles"] = _comma_list(_text_of(boxes["Active Roles:"]))
        if "Birthday:" in boxes:
            item["birthday"] = _text_of(boxes["Birthday:"])
        if "Birthplace:" in boxes:
            item["birthplace"] = _text_of(boxes["Birthplace:"])
        if "Gender:" in boxes:
            item["gender"] = _text_of(boxes["Gender:"])
        if "Height:" in boxes:
            item["height_cm"] = _parse_height_cm(_text_of(boxes["Height:"]))
        if "Weight:" in boxes:
            item["weight_kg"] = _parse_weight_kg(_text_of(boxes["Weight:"]))
        if "Background in sports:" in boxes:
            item["background_in_sports"] = _comma_list(_text_of(boxes["Background in sports:"]))
        if "WWW:" in boxes:
            item["websites"] = [
                href.strip() for href in boxes["WWW:"].css("a::attr(href)").getall()
            ]
        if "Alter egos:" in boxes:
            item["alter_egos"] = _link_texts(boxes["Alter egos:"])
        if "Roles:" in boxes:
            item["roles"] = _parse_role_entries(boxes["Roles:"])
        if "Beginning of in-ring career:" in boxes:
            item["career_start"] = _text_of(boxes["Beginning of in-ring career:"])
        if "End of in-ring career:" in boxes:
            item["career_end"] = _text_of(boxes["End of in-ring career:"])
        if "In-ring experience:" in boxes:
            item["career_experience_years"] = _int_prefix(_text_of(boxes["In-ring experience:"]))
        if "Wrestling style:" in boxes:
            item["wrestling_style"] = _comma_list(_text_of(boxes["Wrestling style:"]))
        if "Trainer:" in boxes:
            item["trainers"] = _link_texts(boxes["Trainer:"])
        if "Nicknames:" in boxes:
            item["nicknames"] = [text.strip('"') for text in _br_list(boxes["Nicknames:"])]
        if "Signature moves:" in boxes:
            item["signature_moves"] = _br_list(boxes["Signature moves:"])

        return item
