"""A single graded review — the immutable unit of learning history.

One row is appended per press of Again/Hard/Good/Easy and never updated or
rewritten. The card in ``words`` only ever holds its *current* state, so without
this log a card reviewed ten times flawlessly and one failed ten times are
indistinguishable — both just show ``review_count == 10``.

What that costs to omit is not recoverable later: the grade, the trajectory
through the boxes, and above all *how long the learner actually went between
reviews* are gone the instant the card row is overwritten. That last pair
(``elapsed_seconds`` with ``grade``) is exactly the input a fitted scheduler
needs, so this log is the prerequisite for ever replacing the fixed
:mod:`~app.domain.services.leitner` ladder with a per-learner model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.entities.word import Word
from app.domain.enums import LeitnerBox, ReviewGrade

#: Ceiling for a self-reported answer latency, in milliseconds (10 minutes).
#: The value is client-supplied and therefore untrusted; anything larger is a
#: backgrounded app or a bad clock, not a learner staring at a card, and would
#: skew every average computed over the column.
MAX_LATENCY_MS = 600_000


@dataclass(frozen=True, slots=True)
class ReviewEvent:
    """One graded review. Immutable by construction — history is never edited."""

    user_id: UUID
    word_id: UUID
    #: The deck the card was in *at review time*. Denormalised deliberately:
    #: cards move between decks, and per-deck history should reflect where the
    #: review happened rather than where the card ended up.
    deck_id: UUID
    reviewed_at: datetime
    grade: ReviewGrade
    box_before: LeitnerBox
    box_after: LeitnerBox

    #: Real time since the previous review of this card; ``None`` on the first.
    #: Stored rather than derived because recovering it later needs a window
    #: function over the whole log, and it is free at write time.
    elapsed_seconds: int | None = None
    #: ``reviewed_at - due_at``. Negative when reviewed early. Together with
    #: ``elapsed_seconds`` this is what separates "remembered after 3 days" from
    #: "remembered after 3 weeks" — the difference retention modelling is made of.
    overdue_seconds: int | None = None
    #: Client-reported think time. Optional and untrusted; see ``MAX_LATENCY_MS``.
    latency_ms: int | None = None
    #: Groups the reviews of one sitting. Client-generated, optional.
    session_id: UUID | None = None

    #: Assigned by the database on insert. ``None`` on a not-yet-persisted event;
    #: the write path does not read it back (see ``ReviewEventRepository.add``).
    id: int | None = None

    @classmethod
    def from_review(
        cls,
        word: Word,
        grade: ReviewGrade,
        box_after: LeitnerBox,
        now: datetime,
        *,
        latency_ms: int | None = None,
        session_id: UUID | None = None,
    ) -> ReviewEvent:
        """Capture a review of ``word`` **before** :meth:`Word.apply_review` runs.

        ``word.box``, ``word.due_at`` and ``word.last_reviewed_at`` are all
        overwritten by the grade, so the ordering matters. Building the event
        through this factory is what keeps that requirement in one place instead
        of relying on statement order at every call site.
        """
        return cls(
            user_id=word.user_id,
            word_id=word.id,
            deck_id=word.deck_id,
            reviewed_at=now,
            grade=grade,
            box_before=word.box,
            box_after=box_after,
            elapsed_seconds=_seconds_between(word.last_reviewed_at, now),
            overdue_seconds=_seconds_between(word.due_at, now),
            latency_ms=_clamp_latency(latency_ms),
            session_id=session_id,
        )

    @property
    def is_lapse(self) -> bool:
        return self.grade.is_lapse


def _seconds_between(earlier: datetime | None, later: datetime) -> int | None:
    if earlier is None:
        return None
    return int((later - earlier).total_seconds())


def _clamp_latency(value: int | None) -> int | None:
    if value is None:
        return None
    return max(0, min(value, MAX_LATENCY_MS))
