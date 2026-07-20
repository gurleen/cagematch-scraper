from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from cagematch_scraper.dates import cagematch_date_text, resolve_on_dates


def test_resolve_on_dates_today_tomorrow() -> None:
    now = datetime(2026, 7, 20, 15, 0, tzinfo=ZoneInfo("America/New_York"))
    assert resolve_on_dates("today,tomorrow", now=now) == [
        date(2026, 7, 20),
        date(2026, 7, 21),
    ]


def test_resolve_on_dates_iso_and_cagematch() -> None:
    assert resolve_on_dates("2026-07-20,21.07.2026") == [
        date(2026, 7, 20),
        date(2026, 7, 21),
    ]


def test_resolve_on_dates_dedupes() -> None:
    now = datetime(2026, 7, 20, 15, 0, tzinfo=ZoneInfo("America/New_York"))
    assert resolve_on_dates("today,2026-07-20", now=now) == [date(2026, 7, 20)]


def test_resolve_on_dates_rejects_empty() -> None:
    with pytest.raises(ValueError, match="At least one"):
        resolve_on_dates(" , ")


def test_resolve_on_dates_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unrecognized"):
        resolve_on_dates("next-week")


def test_cagematch_date_text() -> None:
    assert cagematch_date_text(date(2026, 7, 20)) == "20.07.2026"
