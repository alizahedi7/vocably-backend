"""Port: the per-day review rollup the roster reads."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class MemberTotals:
    """One member's standing in one deck, from ``word_progress``."""

    user_id: UUID
    #: Words in this deck the member has met at all. Under lazy creation a
    #: progress row exists only once graded, so this is simply the row count —
    #: an extra ``review_count > 0`` filter would be a no-op costing an index.
    seen: int
    #: Boxes 1–3: met, not settled.
    learning: int
    #: Box 5. Box 4 is deliberately in neither bucket — the client's mastery
    #: percentage is mastered/seen, and 4 is "known, not yet stuck".
    mastered: int
    #: Their most recent review *in this deck*. Not last sign-in: a member who
    #: opens the app daily and never studies this deck is not active in it.
    last_active_at: datetime | None


@dataclass(frozen=True, slots=True)
class MemberWeek:
    """One member's activity since Monday, from ``daily_deck_activity``."""

    user_id: UUID
    reviews: int
    mastered: int


@dataclass(frozen=True, slots=True)
class DayTotals:
    """One learner's whole day, across every deck."""

    reviews: int
    #: Of those, the ones that were *scheduled* — the card was due when it was
    #: answered. Separate from ``reviews`` because "I cleared today's queue"
    #: and "I answered ten cards" are different claims, and the streak's
    #: light-day path turns on the first: a learner practising cards that were
    #: not due has not finished a queue, and a brand-new deck whose cards are
    #: all tomorrow's work has no queue to finish.
    due_reviews: int


class DeckActivityRepository(ABC):
    @abstractmethod
    async def record_review(
        self, user_id: UUID, deck_id: UUID, day: date, *, mastered: bool, was_due: bool
    ) -> None:
        """Increment today's counters, creating the row on first review.

        Rides along on the transaction the grade already opened.
        """

    @abstractmethod
    async def totals_on(self, user_id: UUID, day: date) -> DayTotals:
        """What this learner did on ``day``, across every deck.

        Backs ``reviewed_today``, the daily-goal award and the streak. A
        handful of indexed rows per learner per day, never a scan of the review
        log.
        """

    @abstractmethod
    async def totals_for_deck(self, deck_id: UUID) -> list[MemberTotals]:
        """Every member's seen/learning/mastered — one grouped query, not N."""

    @abstractmethod
    async def week_for_deck(self, deck_id: UUID, since: date) -> list[MemberWeek]:
        """Every member's reviews and masteries since ``since`` — one query."""
