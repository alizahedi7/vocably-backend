"""Port: persistence contract for :class:`~app.domain.entities.word.Word`."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from app.domain.entities.word import Word
from app.domain.enums import LeitnerBox


class WordRepository(ABC):
    @abstractmethod
    async def get(self, word_id: UUID) -> Word | None: ...

    @abstractmethod
    async def list_for_user(
        self,
        user_id: UUID,
        *,
        deck_id: UUID | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Word]: ...

    @abstractmethod
    async def list_due(
        self,
        user_id: UUID,
        now: datetime,
        *,
        deck_id: UUID | None = None,
        limit: int | None = None,
    ) -> list[Word]: ...

    @abstractmethod
    async def add(self, word: Word) -> Word: ...

    @abstractmethod
    async def update(self, word: Word) -> Word: ...

    @abstractmethod
    async def delete(self, word_id: UUID) -> None: ...

    @abstractmethod
    async def box_distribution(self, user_id: UUID) -> dict[LeitnerBox, int]:
        """Return a ``{box: count}`` map across all of the user's words."""

    @abstractmethod
    async def deck_totals(self, user_id: UUID) -> dict[UUID, tuple[int, int]]:
        """Per deck: ``{deck_id: (word_count, summed_box_values)}`` for progress bars."""

    @abstractmethod
    async def deck_due_counts(self, user_id: UUID, now: datetime) -> dict[UUID, int]:
        """Per deck: ``{deck_id: due_word_count}``."""
