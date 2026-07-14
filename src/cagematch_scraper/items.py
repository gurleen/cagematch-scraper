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


class MatchItem(TypedDict, total=False):
    id: str
    event: str
    result: str


class TitleItem(TypedDict, total=False):
    id: str
    name: str
    promotion: str
