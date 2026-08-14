"""Unit tests for the Leitner SRS logic — pure, no I/O."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.domain.enums import LeitnerBox, ReviewGrade
from app.domain.services import leitner
from app.domain.services.calendar import day_start_for


@pytest.mark.parametrize(
    ("current", "grade", "expected"),
    [
        (LeitnerBox.FAMILIAR, ReviewGrade.AGAIN, LeitnerBox.NEW),
        (LeitnerBox.FAMILIAR, ReviewGrade.HARD, LeitnerBox.LEARNING),
        (LeitnerBox.FAMILIAR, ReviewGrade.GOOD, LeitnerBox.KNOWN),
        (LeitnerBox.FAMILIAR, ReviewGrade.EASY, LeitnerBox.MASTERED),
        # clamping at the edges
        (LeitnerBox.NEW, ReviewGrade.HARD, LeitnerBox.NEW),
        (LeitnerBox.KNOWN, ReviewGrade.EASY, LeitnerBox.MASTERED),
        (LeitnerBox.MASTERED, ReviewGrade.GOOD, LeitnerBox.MASTERED),
    ],
)
def test_next_box(current: LeitnerBox, grade: ReviewGrade, expected: LeitnerBox) -> None:
    assert leitner.next_box(current, grade) == expected


def test_review_sets_due_date_from_resulting_box() -> None:
    # Deliberately mid-afternoon. The old assertion used a midnight ``now``,
    # which made ``now + N days`` land on a midnight too and hid the fact that
    # the time of day was being carried forward.
    now = datetime(2026, 7, 10, 14, 30, tzinfo=UTC)
    day_start = datetime(2026, 7, 10, tzinfo=UTC)
    outcome = leitner.review(LeitnerBox.LEARNING, ReviewGrade.GOOD, now, day_start)

    assert outcome.box == LeitnerBox.FAMILIAR
    assert outcome.due_at == day_start + leitner.interval_for(LeitnerBox.FAMILIAR)


@pytest.mark.parametrize(
    "graded_at",
    [
        datetime(2026, 7, 10, 0, 1, tzinfo=UTC),
        datetime(2026, 7, 10, 9, 14, tzinfo=UTC),
        datetime(2026, 7, 10, 19, 2, tzinfo=UTC),
        datetime(2026, 7, 10, 23, 59, tzinfo=UTC),
    ],
)
def test_due_date_is_a_midnight_whatever_the_hour(graded_at: datetime) -> None:
    """The whole bug in one assertion: the clock must not survive the review.

    Four learners studying the same card at four different hours of the same
    day must all see it again at the same moment, or the day's queue grows
    through the afternoon as each one's hour comes round.
    """
    day_start = datetime(2026, 7, 10, tzinfo=UTC)
    outcome = leitner.review(LeitnerBox.NEW, ReviewGrade.GOOD, graded_at, day_start)

    assert outcome.due_at == datetime(2026, 7, 12, tzinfo=UTC)  # box 2 → 2 days


def test_due_date_follows_the_learners_own_midnight() -> None:
    """A Tehran learner's day starts at 20:30 UTC the evening before."""
    tehran = ZoneInfo("Asia/Tehran")
    graded_at = datetime(2026, 7, 10, 18, 0, tzinfo=UTC)  # 22:30 in Tehran
    day_start = day_start_for("Asia/Tehran", graded_at)

    outcome = leitner.review(LeitnerBox.NEW, ReviewGrade.GOOD, graded_at, day_start)

    # Two days on, still at a Tehran midnight — not at 18:00 UTC.
    assert outcome.due_at.astimezone(tehran) == datetime(2026, 7, 12, tzinfo=tehran)
