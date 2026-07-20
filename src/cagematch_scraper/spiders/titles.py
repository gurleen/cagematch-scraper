"""Titles spider — cagematch.net's title objects (section id=5).

Confirmed live against cagematch.net: a promotion's titles live on its own page's
"Titles" tab at `?id=8&nr=<promotion_id>&page=9` — the same promotion-subtab
convention the roster (`page=15`/`16`) and events (`page=4`) tabs use. That page has
two `table.TBase` tables, each preceded by a `div.Caption` ("Active Titles" /
"Inactive Titles"); every data row's first link is `?id=5&nr=<title_id>` (title
objects are section id=5, as referenced by the matches spider's title links).
Columns: `[#, Title, Current champion(s), Since, Rating, Votes]`. There's no
pagination — every title for the promotion is on the one page.

The title's own page (`?id=5&nr=<title_id>`) is fetched per title to pull the full
reign history ("Title Holders" table): one `div.ChampionDetailsText` per reign, whose
`<br>`-separated lines are `#<reign_number>` / champion(s) / `<from> - <to> (<n>
days)` / a "Matches" link / location. A reign's champion line is either a solo
wrestler (`?id=2&nr=`) or a tag-team/stable entity (`?id=28`/`?id=29`) followed by its
members in parentheses; a trailing `(N)` is that wrestler's/team's Nth reign with the
title (absent on a first reign).

Restricted by `Settings.promotion_ids` (default: WWE + AEW) — like wrestlers/matches,
this spider requires at least one promotion id, since that's how it finds titles.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from parsel import Selector

from ..config import Settings
from ..items import TitleItem, TitleReign, TitleReignChampion
from .base import BaseSpider
from .htmlutils import LINK_RE, TEAM_SECTIONS, strip_tags
from .promotions import _parse_rating, _parse_votes

TITLES_URL = "https://www.cagematch.net/?id=8&nr={promotion_id}&page=9"
PROFILE_URL = "https://www.cagematch.net/?id=5&nr={title_id}"
TITLE_LINK_RE = re.compile(r"[?&]id=5&nr=(\d+)")

BR_SPLIT_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
REIGN_NUMBER_RE = re.compile(r"#(\d+)")
# Duration reads "(11 days)"/"(2803 days)"; a same-day reign shows "(<1 day)" (the
# leading "<" makes it 0 full days). "Tage" appears when the page renders in German.
DURATION_RE = re.compile(r"\(\s*(<)?\s*(\d+)\s*(?:days?|Tage)\)")
TRAILING_COUNT_RE = re.compile(r"\((\d+)\)\s*$")
DATE_SEP_RE = re.compile(r"\s+-\s+")
YEAR_RE = re.compile(r"\d{4}")


def _parse_reign(div: Selector) -> TitleReign | None:
    div_html = div.get() or ""
    full_text = strip_tags(div_html)

    number_match = REIGN_NUMBER_RE.search(full_text)
    if number_match is None:
        return None
    reign: TitleReign = {"reign_number": int(number_match.group(1))}

    span = div.css("span.TextBold")
    span_html = span.get() or ""
    span_text = strip_tags(span_html)

    reign_count: int | None = None
    count_match = TRAILING_COUNT_RE.search(span_text)
    if count_match:
        reign_count = int(count_match.group(1))

    team: TitleReignChampion | None = None
    champions: list[TitleReignChampion] = []
    for section, nr, text in LINK_RE.findall(span_html):
        entity: TitleReignChampion = {"id": nr, "name": strip_tags(text)}
        if section in TEAM_SECTIONS:
            team = entity
        elif section == "2":
            champions.append(entity)

    if team is not None:
        team["title_reign_count"] = reign_count
        reign["team"] = team
    elif champions and reign_count is not None:
        # Solo reign: the trailing (N) is that wrestler's own reign count.
        champions[0]["title_reign_count"] = reign_count
    if champions:
        reign["champions"] = champions

    # The date/duration line is the <br> segment holding a "<from> - <to>" range (it
    # carries a 4-digit year, unlike the champion/location lines); the location is the
    # final segment (a bare "-" means unknown).
    segments = [strip_tags(seg) for seg in BR_SPLIT_RE.split(div_html)]
    segments = [seg for seg in segments if seg]
    for seg in segments:
        if DATE_SEP_RE.search(seg) is None or YEAR_RE.search(seg) is None:
            continue
        duration_match = DURATION_RE.search(seg)
        if duration_match is not None:
            reign["duration_days"] = 0 if duration_match.group(1) else int(duration_match.group(2))
            date_part = seg[: duration_match.start()].strip()
        else:
            date_part = re.sub(r"\([^)]*\)\s*$", "", seg).strip()
        pieces = DATE_SEP_RE.split(date_part, maxsplit=1)
        reign["from_date"] = pieces[0].strip() or None
        to_date = pieces[1].strip() if len(pieces) > 1 else ""
        reign["to_date"] = None if to_date.lower() == "today" else (to_date or None)
        break

    if segments:
        location = segments[-1]
        if (
            location
            and location != "-"
            and location != "Matches"
            and not (DATE_SEP_RE.search(location) and YEAR_RE.search(location))
        ):
            reign["location"] = location

    return reign


class TitlesSpider(BaseSpider):
    name = "titles"
    fetch_profile = True

    def __init__(self, settings: Settings):
        promotion_ids = settings.promotion_id_list()
        if not promotion_ids:
            raise ValueError(
                "titles spider requires at least one promotion id "
                "(set CAGEMATCH_PROMOTION_IDS)"
            )
        self.promotion_ids = promotion_ids
        self._seen: set[str] = set()

    def should_skip_resume(self, existing: dict, item: dict | None = None) -> bool:
        """Refresh active titles under `--resume` so new reigns land nightly.

        Inactive belts almost never gain reigns; skipping them keeps resume cheap.
        Prefer the listing page's `status` when present — the stored JSONL row can
        lag a reactivation.
        """
        status = ""
        if item is not None:
            status = str(item.get("status") or "")
        if not status:
            status = str(existing.get("status") or "")
        return status.strip().lower() == "inactive"

    def start_requests(self) -> Iterable[str]:
        for promotion_id in self.promotion_ids:
            yield TITLES_URL.format(promotion_id=promotion_id)

    def parse(self, selector: Selector, url: str) -> Iterable[TitleItem]:
        promo_match = re.search(r"[?&]nr=(\d+)", url)
        promotion_id = promo_match.group(1) if promo_match else ""

        for table in selector.css("table.TBase"):
            caption = table.xpath("(./preceding::div[contains(@class,'Caption')])[last()]")
            caption_text = " ".join(caption.css("::text").getall()).strip().lower()
            status = "inactive" if "inactive" in caption_text else "active"

            for row in table.css("tr")[1:]:
                link = next(
                    (a for a in row.css("a") if TITLE_LINK_RE.search(a.attrib.get("href", ""))),
                    None,
                )
                if link is None:
                    continue
                title_id = TITLE_LINK_RE.search(link.attrib["href"]).group(1)
                if title_id in self._seen:
                    continue

                name = "".join(link.css("::text").getall()).strip()
                if not name:
                    continue
                self._seen.add(title_id)

                cells = [" ".join(c.css("::text").getall()).strip() for c in row.css("td")]
                item: TitleItem = {
                    "id": title_id,
                    "name": name,
                    "profile_url": PROFILE_URL.format(title_id=title_id),
                    "promotion": promotion_id,
                    "status": status,
                }
                # Columns: [#, Title, Current champion(s), Since, Rating, Votes].
                if len(cells) >= 6:
                    item["champion_since"] = cells[3] or None
                    item["rating"] = _parse_rating(cells[4])
                    item["votes"] = _parse_votes(cells[5])
                yield item

    def parse_profile(self, selector: Selector, item: dict) -> dict:
        reigns: list[TitleReign] = []
        for div in selector.css("div.ChampionDetailsText"):
            reign = _parse_reign(div)
            if reign is not None:
                reigns.append(reign)
        item["reigns"] = reigns
        return item
