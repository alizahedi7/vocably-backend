"""Unit tests for the streak rules in :mod:`app.domain.services.streak`.

The rule these pin: **the streak advances when the day's goal is met, at most
once per local day.** Grading a card is no longer what moves it — see
``tests/api/test_streak.py`` for the half of that which needs a server.
"""

from __future__ import annotations

from datetime import date

from app.domain.entities.user import User
from app.domain.services.streak import DayState, Streak, advanced, settle, state_of

MONDAY = date(2026, 7, 6)
TUESDAY = date(2026, 7, 7)
WEDNESDAY = date(2026, 7, 8)
NEXT_MONDAY = date(2026, 7, 13)


# ── settle: what the streak *is*, before the learner does anything ──


def test_a_day_already_counted_is_left_alone() -> None:
    streak = Streak(days=3, last_day=TUESDAY, banked_on=TUESDAY)
    assert settle(streak, TUESDAY, due_today=5) == streak


def test_yesterday_counted_so_today_is_merely_open() -> None:
    """The streak is alive and unchanged; today has simply not been won yet."""
    streak = Streak(days=3, last_day=MONDAY, banked_on=MONDAY)
    assert settle(streak, TUESDAY, due_today=5) == streak


def test_a_day_with_nothing_due_preserves_the_streak_without_advancing_it() -> None:
    """Nothing was asked, so nothing was failed — and nothing was achieved."""
    streak = Streak(days=3, last_day=MONDAY, banked_on=MONDAY)
    settled = settle(streak, TUESDAY, due_today=0)
    assert settled.days == 3
    assert settled.last_day == TUESDAY
    # Not banked: a rest day is preserved, never celebrated.
    assert settled.banked_on == MONDAY


def test_a_missed_day_lapses_on_read() -> None:
    """The bug this replaced: the number stayed at 3 until the next review.

    It was never brought up to date on a day nobody studied, so a learner was
    shown a streak they had already lost, at exactly the moment they could
    still have saved it.
    """
    streak = Streak(days=3, last_day=MONDAY, banked_on=MONDAY)
    assert settle(streak, WEDNESDAY, due_today=5).days == 0


def test_a_run_of_unattended_rest_days_still_lapses() -> None:
    """Documented, not accidental: a rest day is credited to whoever turns up.

    Reviewing a card rewrites its ``due_at``, so what was due on a past day
    cannot be reconstructed — an unattended zero-due day is not excusable after
    the fact.
    """
    streak = Streak(days=9, last_day=MONDAY, banked_on=MONDAY)
    assert settle(streak, NEXT_MONDAY, due_today=0).days == 0


def test_unknown_dueness_never_credits_a_rest_day_and_never_lapses_early() -> None:
    """``None`` is what ``/users/me`` passes: it cannot afford the due query."""
    streak = Streak(days=3, last_day=MONDAY, banked_on=MONDAY)
    assert settle(streak, TUESDAY, due_today=None) == streak
    assert settle(streak, WEDNESDAY, due_today=None).days == 0


def test_nothing_to_settle_without_a_streak() -> None:
    assert settle(Streak(), WEDNESDAY, due_today=0) == Streak()


# ── advanced: the value a banked day takes ──


def test_consecutive_days_increment() -> None:
    assert advanced(Streak(days=3, last_day=MONDAY), TUESDAY) == 4


def test_a_rest_day_keeps_the_chain_intact() -> None:
    """Monday banked, Tuesday rested, Wednesday banked → four, not one."""
    rested = settle(Streak(days=3, last_day=MONDAY, banked_on=MONDAY), TUESDAY, due_today=0)
    assert advanced(rested, WEDNESDAY) == 4


def test_banking_twice_in_one_day_would_still_only_be_one_more() -> None:
    """The lock is ``banked_on``; this is the arithmetic behind it."""
    assert advanced(Streak(days=3, last_day=TUESDAY, banked_on=TUESDAY), TUESDAY) == 4


def test_a_gap_starts_again_at_one() -> None:
    assert advanced(Streak(days=9, last_day=MONDAY), WEDNESDAY) == 1


def test_a_first_ever_day_starts_at_one() -> None:
    assert advanced(Streak(), MONDAY) == 1


# ── state_of: how today reads on screen ──


def test_banked_is_read_from_the_record_of_banking() -> None:
    streak = Streak(days=4, last_day=TUESDAY, banked_on=TUESDAY)
    assert state_of(streak, TUESDAY, due_today=0) is DayState.BANKED
    assert state_of(streak, TUESDAY, due_today=9) is DayState.BANKED


def test_work_outstanding_reads_as_open() -> None:
    streak = Streak(days=4, last_day=MONDAY, banked_on=MONDAY)
    assert state_of(streak, TUESDAY, due_today=9) is DayState.OPEN


def test_nothing_due_reads_as_a_rest_day() -> None:
    streak = Streak(days=4, last_day=MONDAY, banked_on=MONDAY)
    assert state_of(streak, TUESDAY, due_today=0) is DayState.REST


# ── the entity's own, much smaller, job ──


def test_note_study_day_records_a_study_day_and_not_a_streak() -> None:
    user = User(streak=3, last_studied_on=MONDAY)
    assert user.note_study_day(TUESDAY) is True
    assert user.last_studied_on == TUESDAY
    # Untouched: one card is a study day, not necessarily a streak day.
    assert user.streak == 3
    assert user.streak_last_day is None


def test_note_study_day_is_a_noop_on_the_same_day() -> None:
    user = User(last_studied_on=TUESDAY)
    assert user.note_study_day(TUESDAY) is False


def test_streak_state_reads_the_three_columns() -> None:
    user = User(streak=5, streak_last_day=TUESDAY, streak_banked_on=MONDAY)
    assert user.streak_state == Streak(days=5, last_day=TUESDAY, banked_on=MONDAY)
