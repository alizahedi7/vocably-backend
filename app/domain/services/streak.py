"""When the streak moves, and what a day is worth.

One rule, stated once: **the streak advances when the day's goal is met, at
most once per local calendar day.** Everything else in this module is the
arithmetic of days.

Two things here are worth knowing before changing any of it.

*The streak is settled on read.* It used to move only when a card was graded,
so a learner who missed two days was shown the old number — correct-looking,
stale, and wrong — until their next review silently reset it to 1. They were
never told they had lost it, and the figure they saw while they could still
have saved it was a lie. :func:`settle` is what a reader asks before drawing
the number.

*A rest day is credited only to someone who turns up.* A day with nothing due
asks nothing of the learner, so failing it would punish them for the
scheduler's own choice — but it cannot be excused after the fact either:
reviewing a card rewrites its ``due_at``, so what was due on a past day is not
reconstructible from ``word_progress``. The only honest reading left is that a
rest day counts when the app is opened on it, which is also the incentive worth
having. Three days away with nothing due still lapses.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum


class DayState(StrEnum):
    """Where today stands with the streak. The client draws every state."""

    #: Work was asked for today and is not done. The streak is at risk.
    OPEN = "open"
    #: The goal was met today; the streak has moved and is locked until tomorrow.
    BANKED = "banked"
    #: Nothing was due. Preserved, not advanced — and never celebrated.
    REST = "rest"


@dataclass(frozen=True, slots=True)
class Streak:
    days: int = 0
    #: The last day that counted — banked *or* rested.
    last_day: date | None = None
    #: The last day the goal was actually met. This is what makes banking
    #: idempotent: a second session cannot collect the same day twice.
    banked_on: date | None = None


def settle(streak: Streak, today: date, due_today: int | None) -> Streak:
    """What the streak *is*, before the learner does anything today.

    ``due_today`` is how many cards are due, and ``None`` means "not known
    here" — the caller could not afford the query, or has no business issuing
    it. Unknown is treated as "something was asked", which is the safe
    direction: it can never wrongly credit a rest day, and it can never wrongly
    end a streak that is merely a day old.
    """
    last = streak.last_day
    if streak.days == 0 or last is None:
        return streak
    gap = (today - last).days
    if gap <= 0:
        return streak  # today already counts
    if gap == 1:
        if due_today == 0:
            return replace(streak, last_day=today)  # a rest day, preserved
        return streak  # yesterday counted; today is still open
    return replace(streak, days=0)  # lapsed


def state_of(streak: Streak, today: date, due_today: int) -> DayState:
    """How today should read on screen.

    Banked is read from :attr:`Streak.banked_on` and nowhere else. A separate
    "is the goal met" answer was tried here and removed: it let a read report a
    day as won that no write had ever banked — the streak and the badge saying
    different things about the same day — and the only way they can be made to
    agree is for one of them to be the record of the other.
    """
    if streak.banked_on == today:
        return DayState.BANKED
    if due_today == 0:
        return DayState.REST
    return DayState.OPEN


def advanced(streak: Streak, today: date) -> int:
    """The value :attr:`Streak.days` takes when ``today`` is banked.

    Consecutive with the last counted day → one more; anything else starts
    again at 1. Stated here so the SQL that performs the update atomically and
    the tests that reason about it cannot drift apart.
    """
    last = streak.last_day
    if last is not None and (today - last).days <= 1:
        return streak.days + 1
    return 1
