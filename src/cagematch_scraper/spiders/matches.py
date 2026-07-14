"""Matches spider — cagematch.net's events database (id=1).

Confirmed live against cagematch.net: a promotion's event list lives at
`?id=8&nr=<promotion_id>&page=4&vYear=<year>`, paginated via `s=<row offset>` in steps
of 100. Rows are `[#, date, promo-logo, event name, location, "Card" link, blank,
rating, votes]`. Each configured promotion (`Settings.promotion_ids`) is walked one
year at a time from `Settings.matches_since_year` through the current year, via
`next_page_url` since the page count per (promotion, year) isn't known upfront.

An event's own page (`?id=1&nr=<event_id>`, no `page=` param — cagematch's own nav
calls this tab "Results") lists every match on the card with full results, not just
the lineup: `<div class="Match">` blocks each containing:
- `MatchType`: e.g. "Singles Match", or `<a href="?id=5&nr=..">Title Name</a> Match`
  for title matches.
- `MatchResults`: "`<winner>` defeats `<loser1>` and `<loser2>` (`<duration>`)", with
  `(c)` marking the pre-match champion and a `MatchTitleChange` span for title changes.
  Non-decisive finishes (draws, no-contests) use "`<sideA>` vs. `<sideB>` - `<finish
  note>`" instead — no "defeats", so no `winners`/`losers` (see `sides` instead).
  Wrestlers inside a "(w/`<name>`)" span are valets, not competitors, and are recorded
  separately.
- `MatchRecommendedLine` (matchguide rating) and `MatchNotes` (e.g. elimination order),
  both optional.

Because the event's own page already has full results, this spider — unlike
promotions/wrestlers — has no reason to fetch anything beyond it: `fetch_profile`
enriches each event stub (from the list page) with its full `matches` array in one
extra request per event, and that's the complete dataset. Each output line is one
event with all of its matches nested under `matches`, not one line per match — cheaper
to write and avoids repeating event fields on every match.

Restricted by `Settings.promotion_ids` (default: WWE + AEW) — like the wrestlers
spider, this one requires at least one promotion id, since that's how it finds events.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timezone

from parsel import Selector

from ..config import Settings
from ..items import MatchItem, MatchRecord, MatchSide
from .base import BaseSpider
from .htmlutils import br_list, strip_tags
from .promotions import _parse_rating, _parse_votes

PAGE_SIZE = 100
EVENTS_URL = "https://www.cagematch.net/?id=8&nr={promotion_id}&page=4&vYear={year}&s=0"
EVENT_URL = "https://www.cagematch.net/?id=1&nr={event_id}"
EVENT_LINK_RE = re.compile(r"^\?id=1&nr=(\d+)$")

TITLE_LINK_RE = re.compile(r'<a href="\?id=5&amp;nr=(\d+)[^"]*">(.*?)</a>')
LINK_RE = re.compile(r'<a href="\?id=(\d+)&amp;nr=(\d+)[^"]*">(.*?)</a>')
VALET_RE = re.compile(r"\(w/(.*?)\)")
DURATION_RE = re.compile(r"\((\d{1,3}:\d{2}(?::\d{2})?)\)")
TITLE_CHANGE_RE = re.compile(r'\s*-\s*<span class="MatchTitleChange">.*?</span>')
DEFEATS_RE = re.compile(r"\bdefeats?\b")
VS_SPLIT_RE = re.compile(r"\s*vs\.?\s*")
AND_SPLIT_RE = re.compile(r"\s+and\s+")
MATCH_RATING_RE = re.compile(r"Matchguide Rating:\s*([\d.]+)\s*based on\s*(\d+)\s*votes")
TEAM_SECTIONS = {"28", "29"}


def _parse_match_type(selector: Selector) -> tuple[str | None, str | None, str]:
    div_html = selector.get() or ""
    full_text = strip_tags(div_html)
    match = TITLE_LINK_RE.search(div_html)
    if not match:
        return None, None, full_text
    title_id = match.group(1)
    title_name = strip_tags(match.group(2))
    return title_id, title_name, full_text.replace(title_name, "", 1).strip()


def _parse_side(fragment_html: str) -> MatchSide:
    is_champion = "(c)" in strip_tags(fragment_html)

    valets: list[dict] = []

    def _collect_valets(m: re.Match) -> str:
        for section, nr, text in LINK_RE.findall(m.group(0)):
            if section == "2":
                valets.append({"id": nr, "name": strip_tags(text)})
        return ""

    main_html = VALET_RE.sub(_collect_valets, fragment_html)

    wrestlers: list[dict] = []
    teams: list[dict] = []
    for section, nr, text in LINK_RE.findall(main_html):
        name = strip_tags(text)
        if section == "2":
            wrestlers.append({"id": nr, "name": name})
        elif section in TEAM_SECTIONS:
            teams.append({"id": nr, "name": name})

    side: MatchSide = {}
    if wrestlers:
        side["wrestlers"] = wrestlers
    if teams:
        side["teams"] = teams
    if valets:
        side["valets"] = valets
    if is_champion:
        side["is_champion"] = True
    return side


def _parse_match_results(selector: Selector) -> dict:
    div_html = selector.get() or ""

    title_change = bool(TITLE_CHANGE_RE.search(div_html))
    div_html = TITLE_CHANGE_RE.sub("", div_html)

    duration = None
    duration_matches = list(DURATION_RE.finditer(div_html))
    if duration_matches:
        last = duration_matches[-1]
        duration = last.group(1)
        div_html = div_html[: last.start()] + div_html[last.end() :]

    finish_note = None
    if " - " in strip_tags(div_html):
        main_html, _, note_html = div_html.rpartition(" - ")
        finish_note = strip_tags(note_html) or None
        div_html = main_html

    result: dict = {"duration": duration, "title_change": title_change, "finish_note": finish_note}

    decisive = DEFEATS_RE.search(div_html)
    if decisive:
        winner_html = div_html[: decisive.start()]
        losers_html = div_html[decisive.end() :]
        result["result"] = "decisive"
        result["winners"] = _parse_side(winner_html)
        result["losers"] = [_parse_side(h) for h in AND_SPLIT_RE.split(losers_html)]
    else:
        sides_html = VS_SPLIT_RE.split(div_html)
        result["result"] = "no_decision" if len(sides_html) > 1 else "unknown"
        result["sides"] = [_parse_side(h) for h in sides_html]

    return result


def _parse_match_rating(selector: Selector | None) -> tuple[float | None, int | None]:
    if selector is None:
        return None, None
    text = strip_tags(selector.get() or "")
    match = MATCH_RATING_RE.search(text)
    if not match:
        return None, None
    return float(match.group(1)), int(match.group(2))


class MatchesSpider(BaseSpider):
    name = "matches"
    fetch_profile = True

    def __init__(self, settings: Settings):
        promotion_ids = settings.promotion_id_list()
        if not promotion_ids:
            raise ValueError(
                "matches spider requires at least one promotion id "
                "(set CAGEMATCH_PROMOTION_IDS)"
            )
        self.promotion_ids = promotion_ids
        current_year = datetime.now(timezone.utc).year
        self.years = list(range(settings.matches_since_year, current_year + 1))
        self._seen: set[str] = set()

    def start_requests(self) -> Iterable[str]:
        for promotion_id in self.promotion_ids:
            for year in self.years:
                yield EVENTS_URL.format(promotion_id=promotion_id, year=year)

    def next_page_url(self, selector: Selector, url: str) -> str | None:
        tables = selector.css("table.TBase")
        rows = tables[0].css("tr")[1:] if tables else []
        if len(rows) < PAGE_SIZE:
            return None
        match = re.search(r"[?&]s=(\d+)", url)
        offset = int(match.group(1)) if match else 0
        return re.sub(r"([?&]s=)\d+", rf"\g<1>{offset + PAGE_SIZE}", url)

    def parse(self, selector: Selector, url: str) -> Iterable[MatchItem]:
        promo_match = re.search(r"[?&]nr=(\d+)", url)
        promotion_id = promo_match.group(1) if promo_match else ""

        tables = selector.css("table.TBase")
        rows = tables[0].css("tr")[1:] if tables else []
        for row in rows:
            event_link = next(
                (a for a in row.css("a") if EVENT_LINK_RE.match(a.attrib.get("href", ""))), None
            )
            if event_link is None:
                continue
            event_id = EVENT_LINK_RE.match(event_link.attrib["href"]).group(1)
            if event_id in self._seen:
                continue

            name = "".join(event_link.css("::text").getall()).strip()
            if not name:
                continue
            self._seen.add(event_id)

            cells = [" ".join(c.css("::text").getall()).strip() for c in row.css("td")]

            item: MatchItem = {
                "id": event_id,
                "name": name,
                "profile_url": EVENT_URL.format(event_id=event_id),
                "promotion": promotion_id,
            }
            if len(cells) > 1:
                item["date"] = cells[1]
            if len(cells) > 4:
                item["location"] = cells[4]
            if len(cells) >= 2:
                item["event_rating"] = _parse_rating(cells[-2])
                item["event_votes"] = _parse_votes(cells[-1])
            yield item

    def parse_profile(self, selector: Selector, item: dict) -> dict:
        matches: list[MatchRecord] = []
        for index, match_div in enumerate(selector.css("div.Match"), start=1):
            type_selectors = match_div.css("div.MatchType")
            results_selectors = match_div.css("div.MatchResults")
            if not results_selectors:
                continue

            title_id, title_name, match_type = _parse_match_type(type_selectors[0])
            record: MatchRecord = {
                "match_index": index,
                "match_type": match_type,
                "title_id": title_id,
                "title_name": title_name,
                **_parse_match_results(results_selectors[0]),
            }

            rating_selectors = match_div.css("div.MatchRecommendedLine")
            match_rating, match_votes = _parse_match_rating(
                rating_selectors[0] if rating_selectors else None
            )
            if match_rating is not None:
                record["match_rating"] = match_rating
            if match_votes is not None:
                record["match_votes"] = match_votes

            notes_selectors = match_div.css("div.MatchNotes")
            if notes_selectors:
                notes = br_list(notes_selectors[0].get() or "")
                if notes:
                    record["notes"] = notes

            matches.append(record)

        item["matches"] = matches
        return item
