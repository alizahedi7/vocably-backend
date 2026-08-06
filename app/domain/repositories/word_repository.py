"""Port: persistence contract for :class:`~app.domain.entities.word.Word`.

Content only. A learner's boxes and due dates are
:class:`~app.domain.repositories.word_progress_repository.WordProgressRepository`'s
job — including every read that needs both, which returns a ``StudiedWord``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.entities.word import Word


class WordRepository(ABC):
    @abstractmethod
    async def get(self, word_id: UUID) -> Word | None: ...

    @abstractmethod
    async def list_in_deck(
        self,
        deck_id: UUID,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Word]: ...

    @abstractmethod
    async def add(self, word: Word) -> Word: ...

    @abstractmethod
    async def update(self, word: Word) -> Word:
        """Persist a content edit.

        Also re-points ``word_progress.deck_id`` when the card moves deck: that
        column is a live mirror, and every per-deck aggregate reads it instead
        of joining ``words``.
        """

    @abstractmethod
    async def delete(self, word_id: UUID) -> None: ...
