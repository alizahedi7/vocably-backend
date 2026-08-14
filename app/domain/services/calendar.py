"""Day and week boundaries, computed in the learner's own timezone.

Every "today" in this product is a local question. A streak, a daily goal and
the roster's weekly figures all turn on where a day starts, and the server had
no answer — ``date.today()`` in UTC gives a learner in Tehran a day boundary at
03:30 local, which silently breaks a streak they kept.

So: one module, and nothing else calls ``date.today()``. Weeks start Monday,
matching the client's ``weekStart()``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: What an unknown or unset timezone falls back to. UTC rather than a guess:
#: being wrong by a fixed, known offset is recoverable, and inventing a locale
#: from a phone prefix is not.
DEFAULT_TIMEZONE = "UTC"


def zone_for(timezone: str | None) -> ZoneInfo:
    """Resolve an IANA name, falling back to UTC rather than raising.

    A bad name reaches here from a client that shipped one, and a home screen
    that 500s is worse than a day boundary in the wrong place. Validation
    belongs at the write, not here.
    """
    if not timezone:
        return ZoneInfo(DEFAULT_TIMEZONE)
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def is_valid_timezone(timezone: str) -> bool:
    """Whether ``timezone`` is a resolvable IANA name — for validating a write."""
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def today_for(timezone: str | None, now: datetime | None = None) -> date:
    """The learner's current calendar day."""
    moment = now or datetime.now(UTC)
    return moment.astimezone(zone_for(timezone)).date()


def week_start_for(timezone: str | None, now: datetime | None = None) -> date:
    """The Monday of the learner's current week."""
    day = today_for(timezone, now)
    return day - timedelta(days=day.weekday())


def day_bounds(day: date, timezone: str | None) -> tuple[datetime, datetime]:
    """The UTC half-open interval ``[start, end)`` covering ``day`` locally.

    Returned as UTC instants because that is what the stored timestamps are
    compared against; doing the conversion here keeps every caller from
    reinventing it, and from getting a DST-shortened day subtly wrong.
    """
    zone = zone_for(timezone)
    start = datetime.combine(day, datetime.min.time(), tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def day_start_for(timezone: str | None, now: datetime | None = None) -> datetime:
    """The UTC instant the learner's current day began.

    The boundary "today" is measured from — what makes a word added this
    afternoon and one added at breakfast equally *not due until tomorrow*.
    """
    moment = now or datetime.now(UTC)
    start, _ = day_bounds(today_for(timezone, moment), timezone)
    return start


def day_end_for(timezone: str | None, now: datetime | None = None) -> datetime:
    """The UTC instant the learner's current day ends — i.e. tomorrow's start.

    The half-open partner of :func:`day_start_for`, and the boundary *dueness*
    is measured against. A card is due today when it is due at any point before
    this instant, which is what makes a day's queue the same size at breakfast
    and at midnight. Comparing against ``now`` instead lets a card scheduled
    days ago at 19:02 appear at 19:02 today, so the count climbs all evening.

    Exclusive: a card falling exactly on this instant belongs to tomorrow.
    """
    moment = now or datetime.now(UTC)
    _, end = day_bounds(today_for(timezone, moment), timezone)
    return end


def week_bounds(day: date, timezone: str | None) -> tuple[datetime, datetime]:
    """The UTC interval covering the Monday-to-Sunday week containing ``day``."""
    monday = day - timedelta(days=day.weekday())
    start, _ = day_bounds(monday, timezone)
    _, end = day_bounds(monday + timedelta(days=6), timezone)
    return start, end
