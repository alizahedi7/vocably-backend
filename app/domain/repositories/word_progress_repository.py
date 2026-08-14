"""Port: reading and writing one learner's study state.

Everything that needs a learner's boxes lives here; :class:`WordRepository` is
left with the card's content. That split is what keeps the layering honest —
``WordService`` never touches progress, ``StudyService`` never touches bare
content — and it is why the aggregate methods moved off the word repository.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.entities.studied_word import StudiedWord
from app.domain.entities.word_progress import WordProgress
from app.domain.enums import LeitnerBox


@dataclass(frozen=True, slots=True)
class DeckBoxTally:
    """One ``(deck, box)`` cell of a learner's library. At most five per deck."""

    deck_id: UUID
    box: LeitnerBox
    #: False only for a self-paced deck's cards the learner has not started.
    #: Those count towards the deck's size and nothing else — not the boxes,
    #: not memory strength, not a single due number.
    started: bool
    word_count: int
    due_count: int


class WordProgressRepository(ABC):
    """
    ``day_start`` and ``day_end`` on the reads are the UTC instants the
    learner's current day began and ends (``calendar.day_start_for`` /
    ``day_end_for``) — the half-open ``[start, end)`` covering their today.

    ``day_start`` decides when a card nobody has answered becomes due: the day
    *after* it was added, never the same day — writing a word down is the first
    exposure, and testing it thirty seconds later measures nothing.
    ``day_end`` decides when an already-scheduled card becomes due, and it is a
    *day* boundary rather than the current instant on purpose: a card graded at
    19:02 is due for the whole of its day, not from 19:02 onwards, or the
    home-screen count climbs through the afternoon.

    Timezones belong to the service layer, so passing ``None`` accepts a UTC
    boundary and is only honest for reads that do not turn on due dates at all.
    """

    @abstractmethod
    async def get_for_user(
        self,
        word_id: UUID,
        user_id: UUID,
        *,
        day_start: datetime | None = None,
    ) -> StudiedWord | None:
        """The card plus this learner's state, or ``None`` if they cannot see it.

        Visibility is deck membership. Returning ``None`` rather than raising
        keeps the "not a member" and "no such word" cases indistinguishable to
        the caller, which is what stops a 403 confirming that another class's
        card exists.
        """

    @abstractmethod
    async def list_due(
        self,
        user_id: UUID,
        now: datetime,
        *,
        deck_id: UUID | None = None,
        limit: int | None = None,
        day_start: datetime | None = None,
        day_end: datetime | None = None,
    ) -> list[StudiedWord]: ...

    @abstractmethod
    async def list_for_user(
        self,
        user_id: UUID,
        *,
        deck_id: UUID | None = None,
        limit: int | None = None,
        offset: int = 0,
        started_only: bool = False,
        day_start: datetime | None = None,
    ) -> list[StudiedWord]:
        """Every card the learner can see, started or not.

        ``started_only`` is for callers that are choosing cards to *study*: the
        deck screen lists a self-paced deck in full, but a session must never
        deal a card nobody has started.
        """
        ...

    @abstractmethod
    async def tally_by_deck_and_box(
        self,
        user_id: UUID,
        now: datetime,
        *,
        day_start: datetime | None = None,
        day_end: datetime | None = None,
    ) -> list[DeckBoxTally]:
        """Every home-screen and deck-list number, in one query.

        Replaces the three per-user aggregates that used to sit on
        ``WordRepository`` and the unbounded ``list_due`` fetch that
        ``get_overview`` counted in Python. Words with no progress row are
        tallied as box 1 and counted as due — that is what "never studied" means,
        *unless* the membership is self-paced, in which case they come back as
        ``started=False`` cells with no due count at all.
        """

    @abstractmethod
    async def list_unstarted(
        self,
        user_id: UUID,
        deck_id: UUID,
        *,
        unit_id: UUID | None = None,
        word_ids: list[UUID] | None = None,
        limit: int | None = None,
    ) -> list[UUID]:
        """Ids of a self-paced deck's cards this learner has not started.

        In the deck's own order, so "add the next ten" adds the ten they can
        see at the top of the list. Always empty for a deck that is not
        self-paced — there is nothing to start there.
        """

    @abstractmethod
    async def start_words(
        self,
        user_id: UUID,
        deck_id: UUID,
        word_ids: list[UUID],
        now: datetime,
        *,
        due_at: datetime,
    ) -> int:
        """Put cards into the learner's boxes at box 1, due at ``due_at``.

        Returns how many landed.

        Idempotent: a card already started is skipped rather than reset.
        """

    @abstractmethod
    async def unstart_words(self, user_id: UUID, word_ids: list[UUID]) -> int:
        """Undo a start, for cards with no reviews yet. Returns how many.

        Never touches a card that has been answered: that row holds real
        progress, and an undo is not a delete.
        """

    @abstractmethod
    async def record_grade(self, progress: WordProgress, *, is_lapse: bool) -> WordProgress:
        """Persist a graded review, returning the row as it now stands.

        Creates the row on a learner's first sight of the word. Counters are
        incremented in the database rather than overwritten, so two grades
        arriving at once cannot land as one — see the adapter for why that
        matters.
        """
