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


class WrestlerItem(TypedDict, total=False):
    id: str
    name: str
    profile_url: str


class MatchItem(TypedDict, total=False):
    id: str
    event: str
    result: str


class TitleItem(TypedDict, total=False):
    id: str
    name: str
    promotion: str
