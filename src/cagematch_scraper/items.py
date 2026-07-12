"""Loose item shapes. Schema is deliberately minimal; refine later once retention is decided."""

from __future__ import annotations

from typing import TypedDict


class PromotionItem(TypedDict, total=False):
    id: str
    name: str
    profile_url: str
    short_name: str
    status: str
    location: str


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
