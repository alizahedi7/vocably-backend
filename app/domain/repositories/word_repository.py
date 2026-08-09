"""Port: persistence contract for :class:`~app.domain.entities.word.Word`.

Content only. A learner's boxes and due dates are
:class:`~app.domain.repositories.word_progress_repository.WordProgressRepository`'s
job — including every read that needs both, which returns a ``StudiedWord``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from uuid import UUID

from app.domain.entities.word import Word


class WordRepository(ABC):
    @abstractmethod
    async def get(self, word_id: UUID) -> Word | None: ...

    @abstractmethod
    async def list_by_ids(self, word_ids: Sequence[UUID]) -> list[Word]:
        """The cards with these ids, in one query.

        Exists for the admin review screen, which shows a page of build items
        beside the card each one produced. Fetching those one at a time would be
        the N+1 the deck roster already has a test guarding against.
        """

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

    # ── phonetic backfill ────────────────────────────────────
    # Two narrow methods rather than a general query, because they are keyed by
    # *term* and not by card: one dictionary call answers for every learner who
    # ever typed that word, which is what keeps the backfill from being one HTTP
    # request per row.

    @abstractmethod
    async def list_terms_missing_phonetic(self, limit: int) -> list[str]:
        """Distinct terms of cards whose ``phonetic`` has never been looked up.

        ``NULL`` means *no answer yet*; the empty string means *the dictionary
        answered, and this word has no transcription* — about a third of them.
        Only the former is returned, so the common case of "covered, but no IPA"
        is asked about once instead of on every run forever.

        The order is deliberately random. A word the dictionary has no entry at
        all for stays ``NULL`` (see ``set_phonetic_for_term``), and under any
        stable ordering a handful of those would sit at the head of the queue
        and consume every run.
        """

    @abstractmethod
    async def set_phonetic_for_term(self, term: str, phonetic: str) -> int:
        """Record one answer against every unchecked card with that term.

        Returns the number of rows written. Only ``NULL`` rows are touched, so
        this can never overwrite a transcription that arrived with the card, and
        re-running it after a partial failure is safe.
        """
