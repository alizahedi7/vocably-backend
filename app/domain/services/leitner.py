"""Leitner spaced-repetition logic.

Pure functions — no I/O, no framework. This is the heart of the study system and is
trivially unit-testable. Given a card's current box and a review grade, it computes the
new box and the next due date.

Box → interval mapping (days) matches the spirit of the design's grading hints:
``again`` sends a card back to box 1 (see it tomorrow), ``easy`` jumps it two boxes ahead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.enums import LeitnerBox, ReviewGrade

#: How many days until a card in a given box is due again.
BOX_INTERVALS_DAYS: dict[LeitnerBox, int] = {
    LeitnerBox.NEW: 1,
    LeitnerBox.LEARNING: 2,
    LeitnerBox.FAMILIAR: 4,
    LeitnerBox.KNOWN: 9,
    LeitnerBox.MASTERED: 21,
}

_MIN_BOX = int(LeitnerBox.NEW)
_MAX_BOX = int(LeitnerBox.MASTERED)


def _clamp_box(value: int) -> LeitnerBox:
    return LeitnerBox(max(_MIN_BOX, min(_MAX_BOX, value)))


def next_box(current: LeitnerBox, grade: ReviewGrade) -> LeitnerBox:
    """Apply a grade to a box and return the resulting box.

    - ``again`` → back to box 1
    - ``hard``  → down one box (floored at 1)
    - ``good``  → up one box (capped at 5)
    - ``easy``  → up two boxes (capped at 5)
    """
    match grade:
        case ReviewGrade.AGAIN:
            return LeitnerBox.NEW
        case ReviewGrade.HARD:
            return _clamp_box(current - 1)
        case ReviewGrade.GOOD:
            return _clamp_box(current + 1)
        case ReviewGrade.EASY:
            return _clamp_box(current + 2)


def interval_for(box: LeitnerBox) -> timedelta:
    return timedelta(days=BOX_INTERVALS_DAYS[box])


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    box: LeitnerBox
    due_at: datetime


def review(current: LeitnerBox, grade: ReviewGrade, now: datetime) -> ReviewOutcome:
    """Compute the full outcome of grading a card at ``now``."""
    box = next_box(current, grade)
    return ReviewOutcome(box=box, due_at=now + interval_for(box))
