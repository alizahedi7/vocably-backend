"""One learner's spaced-repetition state against one card.

Split out of :class:`~app.domain.entities.word.Word` so that a deck can be
shared as *the same deck*: the card is shared, this is not. Two members hold two
rows against one ``words.id``, and neither can see the other's boxes.

Rows are created **lazily** — only :meth:`grade` ever writes one. A learner who
joins a 500-word class deck and never opens it costs nothing, and the words
still read correctly because a missing row means :meth:`unstudied`: box 1, due
now, nothing recorded. A word nobody has studied is due immediately, which is
exactly what "new" means.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.domain.enums import LeitnerBox, ReviewGrade


@dataclass(frozen=True, slots=True)
class ReviewApplied:
    """What a grade changed, for callers that must react to a transition.

    Returned rather than re-derived: whether this review is the one that reached
    box 5 is only distinguishable *before* :meth:`WordProgress.apply_review`
    runs, and "read the pre-state first" should not be a rule every call site
    has to remember — the same reasoning that put
    :meth:`~app.domain.entities.review_event.ReviewEvent.from_review` in a
    factory.
    """

    #: True only on the review that first reached box 5, never on a later one.
    became_mastered: bool


@dataclass(slots=True)
class WordProgress:
    user_id: UUID = field(default_factory=uuid4)
    word_id: UUID = field(default_factory=uuid4)
    #: Live mirror of ``words.deck_id``, so per-deck aggregates and the member
    #: roster never join ``words`` to recover it. Deliberately the *opposite* of
    #: ``word_reviews.deck_id``, which is frozen at review time: this one is kept
    #: in step by the repository when a card moves deck. Do not unify them.
    deck_id: UUID = field(default_factory=uuid4)

    box: LeitnerBox = LeitnerBox.NEW
    due_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    review_count: int = 0
    last_reviewed_at: datetime | None = None

    # Review summary counters.
    #
    # Every one of these is derivable by replaying ``word_reviews``, and is kept
    # here anyway: they are what the product actually reads ("your hardest
    # words", time-to-mastery), and reading them costs one indexed row instead
    # of an aggregate over the event log. They ride along on the UPDATE the
    # grade already performs, so maintaining them is free.
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

    @classmethod
    def unstudied(
        cls,
        user_id: UUID,
        word_id: UUID,
        deck_id: UUID,
        now: datetime,
    ) -> WordProgress:
        """The value a *missing* row reads as: box 1, due now, nothing recorded.

        Constructed on read and never persisted by itself — only :meth:`grade`
        writes a row, which is what keeps sharing a deck with thirty students
        from writing fifteen thousand rows for people who may never open it.
        """
        return cls(user_id=user_id, word_id=word_id, deck_id=deck_id, due_at=now)

    def is_due(self, now: datetime) -> bool:
        return self.due_at <= now

    def apply_review(
        self,
        grade: ReviewGrade,
        box: LeitnerBox,
        due_at: datetime,
        now: datetime,
    ) -> ReviewApplied:
        """Fold a graded review into this learner's state for the card.

        ``box`` and ``due_at`` come from :mod:`app.domain.services.leitner`, which
        owns the scheduling decision; this method owns only the bookkeeping around
        it. Callers that also record a :class:`~app.domain.entities.review_event.
        ReviewEvent` must build the event *first* — it captures the pre-review
        values this method overwrites.
        """
        became_mastered = box is LeitnerBox.MASTERED and self.mastered_at is None
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
        if became_mastered:
            self.mastered_at = now
        self.last_grade = grade
        self.last_reviewed_at = now
        self.updated_at = now
        return ReviewApplied(became_mastered=became_mastered)

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
