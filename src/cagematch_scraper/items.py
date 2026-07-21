"""Loose item shapes. Schema is deliberately minimal; refine later once retention is decided."""

from __future__ import annotations

from typing import TypedDict


class PromotionNameHistoryEntry(TypedDict, total=False):
    name: str
    from_date: str | None
    to_date: str | None


class PromotionItem(TypedDict, total=False):
    id: str
    name: str
    profile_url: str
    location: str
    active_year_start: int | None
    active_year_end: int | None
    rating: float | None
    votes: int | None
    name_history: list[PromotionNameHistoryEntry]


class WrestlerDateRange(TypedDict, total=False):
    from_date: str | None
    to_date: str | None


class WrestlerRoleEntry(TypedDict, total=False):
    role: str
    date_ranges: list[WrestlerDateRange]


class WrestlerItem(TypedDict, total=False):
    id: str
    name: str
    profile_url: str
    promotions: list[str]
    gender: str
    birthday: str
    birthplace: str
    age: int | None
    height_cm: int | None
    weight_kg: int | None
    background_in_sports: list[str]
    alter_egos: list[str]
    nicknames: list[str]
    signature_moves: list[str]
    wrestling_style: list[str]
    trainers: list[str]
    active_roles: list[str]
    roles: list[WrestlerRoleEntry]
    career_start: str | None
    career_end: str | None
    career_experience_years: int | None
    websites: list[str]
    current_promotion: str | None
    current_brand: str | None
    roster_rating: float | None
    roster_votes: int | None
    career_shows: int | None  # only set for wrestlers found via All-Time Roster


class MatchParticipant(TypedDict, total=False):
    id: str
    name: str


class MatchSide(TypedDict, total=False):
    wrestlers: list[MatchParticipant]
    teams: list[MatchParticipant]
    valets: list[MatchParticipant]
    is_champion: bool


class MatchRecord(TypedDict, total=False):
    match_index: int
    match_type: str
    title_id: str | None
    title_name: str | None
    title_change: bool
    duration: str | None
    result: str  # "decisive" | "no_decision" | "unknown"
    finish_note: str | None  # e.g. "Double Count Out", set for no_decision results
    winners: MatchSide | None
    losers: list[MatchSide]
    sides: list[MatchSide]  # set instead of winners/losers when result != "decisive"
    match_rating: float | None
    match_votes: int | None
    won_rating: str | None  # e.g. "*****1/2", from Wrestling Observer Newsletter
    notes: list[str]


class MatchItem(TypedDict, total=False):
    id: str
    name: str
    profile_url: str
    promotion: str
    date: str
    location: str
    event_rating: float | None
    event_votes: int | None
    event_type: str | None  # e.g. "TV-Show", "Live Event"
    arena: str | None
    broadcast_type: str | None  # e.g. "Live", "Taped"
    broadcast_date: str | None
    tv_network: str | None
    commentators: list[MatchParticipant]
    matches: list[MatchRecord]


class TitleReignChampion(TypedDict, total=False):
    id: str
    name: str
    title_reign_count: int | None  # "(N)" suffix — this wrestler/team's Nth reign
    # *with this specific title*; None on a first reign


class TitleReign(TypedDict, total=False):
    reign_number: int  # page's own sequential "#N" index
    champions: list[TitleReignChampion]  # solo, team's members, or bare co-champions
    team: TitleReignChampion | None  # populated only for the team-entity shape
    from_date: str | None
    to_date: str | None  # None = ongoing ("today" marker on page)
    duration_days: int | None
    location: str | None


class TitleNameHistoryEntry(TypedDict, total=False):
    name: str
    from_date: str | None
    to_date: str | None  # None = current name


class TitlePromotionHistoryEntry(TypedDict, total=False):
    promotion_id: str
    promotion_name: str
    from_date: str | None
    to_date: str | None  # None = current promotion


class TitleItem(TypedDict, total=False):
    id: str
    name: str
    profile_url: str
    promotion: str  # promotion id the titles-list page was fetched for
    rating: float | None
    votes: int | None
    champion_since: str | None  # listing page's "Since" column (raw)
    status: str | None  # "active" | "inactive"
    current_name: str | None
    name_history: list[TitleNameHistoryEntry]
    promotion_history: list[TitlePromotionHistoryEntry]
    reigns: list[TitleReign]


# ---------------------------------------------------------------------------
# The Smackdown Hotel (separate source; pair with Cagematch via slug / DOB / dates)
# ---------------------------------------------------------------------------


class SdhDateRange(TypedDict, total=False):
    from_date: str | None
    to_date: str | None  # None = Present / ongoing


class SdhTitleReignChampion(TypedDict, total=False):
    id: str  # wrestler slug, e.g. "cm-punk"
    name: str
    title_reign_count: int | None  # badge "(N)" — this wrestler's Nth reign


class SdhTitleReign(TypedDict, total=False):
    reign_number: int | None  # page sequence; None on vacant periods
    champions: list[SdhTitleReignChampion]
    from_date: str | None
    to_date: str | None  # derived from next reign's start when possible
    duration_days: int | None
    location: str | None
    event_name: str | None
    event_url: str | None
    notes: str | None
    is_vacant: bool


class SdhTitleNameHistoryEntry(TypedDict, total=False):
    name: str
    from_date: str | None
    to_date: str | None
    image_url: str | None  # belt design for this era (original full-size when available)


class SdhTitleItem(TypedDict, total=False):
    id: str  # "{promotion}/{title-slug}"
    name: str
    profile_url: str
    promotion: str | None
    brand: str | None
    gender: str | None
    date_established: str | None
    current_champion: str | None
    territory: str | None
    title_type: str | None
    image_url: str | None  # current belt image (og:image original)
    name_history: list[SdhTitleNameHistoryEntry]
    reigns: list[SdhTitleReign]


class SdhWrestlerNameHistoryEntry(TypedDict, total=False):
    name: str
    from_date: str | None
    to_date: str | None


class SdhWrestlerPromotionEntry(TypedDict, total=False):
    promotion: str
    brand: str | None
    from_date: str | None
    to_date: str | None


class SdhWrestlerRoleEntry(TypedDict, total=False):
    role: str
    from_date: str | None
    to_date: str | None


class SdhWrestlerAlignmentEntry(TypedDict, total=False):
    alignment: str
    details: str | None
    from_date: str | None
    to_date: str | None


class SdhWrestlerImageEntry(TypedDict, total=False):
    label: str | None  # e.g. "Apr 2026" from the Images History gallery
    image_url: str


class SdhWrestlerCareerAwardEntry(TypedDict, total=False):
    name: str
    url: str | None
    image_url: str | None


class SdhWrestlerHallOfFameEntry(TypedDict, total=False):
    name: str
    category: str | None  # e.g. "Individual"
    year: int | None
    url: str | None
    image_url: str | None


class SdhWrestlerTitleWinEntry(TypedDict, total=False):
    promotion: str
    title: str
    times: int | None
    details: str | None  # e.g. "(with Kofi Kingston)" or "(2008, 2009)"
    title_url: str | None
    image_url: str | None
    source: str  # "auto" (titles-auto) | "manual" (indie titles list)


class SdhWrestlerItem(TypedDict, total=False):
    id: str  # wrestler slug, e.g. "cm-punk"
    name: str
    profile_url: str
    real_name: str | None
    gender: str | None
    birthday: str | None
    age: int | None
    nationality: str | None
    birthplace: str | None
    billed_from: str | None
    height_cm: int | None
    weight_kg: int | None
    image_url: str | None  # og:image (original full-body render when available)
    nicknames: list[str]
    finishers: list[str]
    name_history: list[SdhWrestlerNameHistoryEntry]
    promotions: list[SdhWrestlerPromotionEntry]
    roles: list[SdhWrestlerRoleEntry]
    alignments: list[SdhWrestlerAlignmentEntry]
    images: list[SdhWrestlerImageEntry]
    career_awards: list[SdhWrestlerCareerAwardEntry]
    hall_of_fames: list[SdhWrestlerHallOfFameEntry]
    title_wins: list[SdhWrestlerTitleWinEntry]
    accomplishments: list[str]
