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
    word_count: int
    due_count: int


class WordProgressRepository(ABC):
    @abstractmethod
    async def get_for_user(self, word_id: UUID, user_id: UUID) -> StudiedWord | None:
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
    ) -> list[StudiedWord]: ...

    @abstractmethod
    async def list_for_user(
        self,
        user_id: UUID,
        *,
        deck_id: UUID | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[StudiedWord]: ...

    @abstractmethod
    async def tally_by_deck_and_box(self, user_id: UUID, now: datetime) -> list[DeckBoxTally]:
        """Every home-screen and deck-list number, in one query.

        Replaces the three per-user aggregates that used to sit on
        ``WordRepository`` and the unbounded ``list_due`` fetch that
        ``get_overview`` counted in Python. Words with no progress row are
        tallied as box 1 and counted as due — that is what "never studied" means.
        """

    @abstractmethod
    async def record_grade(self, progress: WordProgress, *, is_lapse: bool) -> WordProgress:
        """Persist a graded review, returning the row as it now stands.

        Creates the row on a learner's first sight of the word. Counters are
        incremented in the database rather than overwritten, so two grades
        arriving at once cannot land as one — see the adapter for why that
        matters.
        """
