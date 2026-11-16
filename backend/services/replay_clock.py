"""The virtual replay clock: real Adelaide time -> 5-minute slot (0..287).

An in-progress slot is not a reading. ``completed_points`` is the number of
finished slots today, i.e. the only slots a response may contain.
"""

import re
from datetime import date as Date, datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

import config
from services.errors import ApiError

ADL = ZoneInfo(config.TIMEZONE)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def now_adl() -> datetime:
    return datetime.now(ADL)


def current_slot(now: Optional[datetime] = None) -> int:
    """Slot containing ``now``: 00:00 -> 0, 10:03 -> 120, 23:59 -> 287."""
    now = now or now_adl()
    return now.hour * 12 + now.minute // 5


def completed_points(now: Optional[datetime] = None) -> int:
    """Number of finished slots today (slots 0 .. completed_points-1)."""
    return current_slot(now)


def slot_time(slot: int) -> time:
    return time(hour=slot // 12, minute=(slot % 12) * 5)


def slot_start(day: Date, slot: int) -> datetime:
    """Start of ``slot`` on ``day`` as a wall-clock Adelaide timestamp.

    ponytail: a fixed 288-slot grid, the same convention battery.py uses for
    every day. On a DST-change day the wall clock repeats/skips an hour; we
    keep 288 evenly named slots rather than 276/300 and never emit a future
    point, which is the behaviour the dashboard needs.
    """
    return datetime.combine(day, slot_time(slot), tzinfo=ADL)


def parse_date(text: str) -> Date:
    """Strict YYYY-MM-DD -> date, else 400 INVALID_DATE."""
    if not isinstance(text, str) or not _DATE_RE.match(text):
        raise ApiError(400, "INVALID_DATE", f"'{text}' is not a valid YYYY-MM-DD date", field="date")
    try:
        return Date.fromisoformat(text)
    except ValueError:
        raise ApiError(400, "INVALID_DATE", f"'{text}' is not a valid calendar date", field="date")


def freshness_seconds(timestamp: Optional[datetime], now: Optional[datetime] = None) -> Optional[int]:
    if timestamp is None:
        return None
    return max(0, int(((now or now_adl()) - timestamp).total_seconds()))
