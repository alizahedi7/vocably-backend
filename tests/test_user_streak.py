"""Unit tests for the study-streak rules on the User entity."""

from __future__ import annotations

from datetime import date

from app.domain.entities.user import User


def test_consecutive_days_increment_streak() -> None:
    user = User(streak=3, last_studied_on=date(2026, 7, 9))
    user.register_study_day(date(2026, 7, 10))
    assert user.streak == 4
    assert user.last_studied_on == date(2026, 7, 10)


def test_same_day_is_noop() -> None:
    user = User(streak=3, last_studied_on=date(2026, 7, 10))
    user.register_study_day(date(2026, 7, 10))
    assert user.streak == 3


def test_gap_resets_streak_to_one() -> None:
    user = User(streak=9, last_studied_on=date(2026, 7, 5))
    user.register_study_day(date(2026, 7, 10))
    assert user.streak == 1


def test_first_ever_study_starts_at_one() -> None:
    user = User(streak=0, last_studied_on=None)
    user.register_study_day(date(2026, 7, 10))
    assert user.streak == 1
