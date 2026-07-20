"""Date helpers for scrape filters (Cagematch text dates + relative tokens)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# Ringside locks / wrestling calendar day boundaries use Eastern time.
DEFAULT_TZ = "America/New_York"


def resolve_on_dates(
    raw: str,
    *,
    tz_name: str = DEFAULT_TZ,
    now: datetime | None = None,
) -> list[date]:
    """Parse a comma-separated `--on-dates` value into calendar dates.

    Accepts `today`, `tomorrow`, ISO `YYYY-MM-DD`, or Cagematch `DD.MM.YYYY`.
    Relative tokens use ``tz_name`` (default America/New_York).
    """
    clock = now or datetime.now(ZoneInfo(tz_name))
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=ZoneInfo(tz_name))
    else:
        clock = clock.astimezone(ZoneInfo(tz_name))
    today = clock.date()

    resolved: list[date] = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        key = token.casefold()
        if key == "today":
            resolved.append(today)
        elif key == "tomorrow":
            resolved.append(today + timedelta(days=1))
        elif len(token) == 10 and token[4] == "-" and token[7] == "-":
            resolved.append(date.fromisoformat(token))
        elif len(token) == 10 and token[2] == "." and token[5] == ".":
            day, month, year = token.split(".")
            resolved.append(date(int(year), int(month), int(day)))
        else:
            raise ValueError(
                f"Unrecognized date {token!r}; use today, tomorrow, YYYY-MM-DD, or DD.MM.YYYY"
            )

    if not resolved:
        raise ValueError("At least one date is required")

    return sorted(set(resolved))


def cagematch_date_text(value: date) -> str:
    """Format a calendar date as Cagematch's `DD.MM.YYYY` text."""
    return value.strftime("%d.%m.%Y")
