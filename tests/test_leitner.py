"""Unit tests for the Leitner SRS logic — pure, no I/O."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.enums import LeitnerBox, ReviewGrade
from app.domain.services import leitner


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
    now = datetime(2026, 7, 10, tzinfo=UTC)
    outcome = leitner.review(LeitnerBox.LEARNING, ReviewGrade.GOOD, now)

    assert outcome.box == LeitnerBox.FAMILIAR
    assert outcome.due_at == now + leitner.interval_for(LeitnerBox.FAMILIAR)
