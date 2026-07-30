"""Word (flashcard) domain entity, including its spaced-repetition state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.domain.enums import LeitnerBox, ReviewGrade


@dataclass(slots=True)
class Word:
    id: UUID = field(default_factory=uuid4)
    deck_id: UUID = field(default_factory=uuid4)
    user_id: UUID = field(default_factory=uuid4)

    # Content
    term: str = ""
    meaning: str = ""
    # Plain-language dictionary definition of the chosen sense — the "DEFINITION"
    # body of the card back. Filled in by AI Card Magic
    # (``MeaningSuggestion.definition``) or written by the learner.
    definition: str | None = None
    example: str | None = None
    # e.g. "verb · progress" or "my definition" — mirrors the design's senseLabel.
    sense_label: str | None = None

    # Spaced-repetition state
    box: LeitnerBox = LeitnerBox.NEW
    due_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    review_count: int = 0
    last_reviewed_at: datetime | None = None

    # Review summary counters.
    #
    # Every one of these is derivable by replaying ``word_reviews``, and is kept
    # here anyway: they are what the product actually reads ("your hardest
    # words", time-to-mastery), and reading them from the card costs one indexed
    # row instead of an aggregate over the event log. They ride along on the
    # UPDATE the grade already performs, so maintaining them is free.
    #: Times this card was graded ``again`` — the difficulty signal.
    lapse_count: int = 0
    #: Length of the current non-lapse run; reset to 0 by ``again``.
    consecutive_correct: int = 0
    #: First time this card was ever graded — the start of its learning curve.
    first_reviewed_at: datetime | None = None
    #: First time it reached box 5. Never cleared: a later lapse does not undo
    #: the fact that mastery was reached, and ``mastered_at - first_reviewed_at``
    #: is the time-to-mastery metric.
    mastered_at: datetime | None = None
    last_grade: ReviewGrade | None = None

    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def is_due(self, now: datetime) -> bool:
        return self.due_at <= now

    def apply_review(
        self,
        grade: ReviewGrade,
        box: LeitnerBox,
        due_at: datetime,
        now: datetime,
    ) -> None:
        """Fold a graded review into this card's state.

        ``box`` and ``due_at`` come from :mod:`app.domain.services.leitner`, which
        owns the scheduling decision; this method owns only the bookkeeping around
        it. Callers that also record a :class:`~app.domain.entities.review_event.
        ReviewEvent` must build the event *first* — it captures the pre-review
        values this method overwrites.
        """
        self.box = box
        self.due_at = due_at
        self.review_count += 1
        if grade.is_lapse:
            self.lapse_count += 1
            self.consecutive_correct = 0
        else:
            self.consecutive_correct += 1
        if self.first_reviewed_at is None:
            self.first_reviewed_at = now
        if box is LeitnerBox.MASTERED and self.mastered_at is None:
            self.mastered_at = now
        self.last_grade = grade
        self.last_reviewed_at = now
        self.updated_at = now

    @property
    def lapse_rate(self) -> float:
        """Share of reviews that were lapses, in ``0.0..1.0``; 0.0 if never reviewed.

        The ranking key for "words you keep forgetting". Raw ``lapse_count``
        would just surface the most-reviewed cards.
        """
        if self.review_count == 0:
            return 0.0
        return self.lapse_count / self.review_count

    @property
    def time_to_mastery(self) -> timedelta | None:
        """How long this card took to first reach box 5, or ``None`` if it hasn't."""
        if self.mastered_at is None or self.first_reviewed_at is None:
            return None
        return self.mastered_at - self.first_reviewed_at
