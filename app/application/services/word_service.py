"""Word (flashcard) use cases, authorized by deck membership.

Reads return a ``StudiedWord`` — the shared card plus the caller's own progress
— so the wire shape is unchanged even though the two halves now live in
different tables. Writes touch only the card, which every editor of the deck
shares.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.application.services.deck_access import DeckAccess
from app.core.exceptions import NotFoundError
from app.domain.entities.studied_word import StudiedWord
from app.domain.entities.word import Word
from app.domain.repositories.deck_member_repository import DeckMemberRepository
from app.domain.repositories.word_progress_repository import WordProgressRepository
from app.domain.repositories.word_repository import WordRepository


class WordService:
    def __init__(
        self,
        words: WordRepository,
        progress: WordProgressRepository,
        members: DeckMemberRepository,
    ) -> None:
        self._words = words
        self._progress = progress
        self._access = DeckAccess(members)

    async def list_words(
        self,
        user_id: UUID,
        *,
        deck_id: UUID | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[StudiedWord]:
        if deck_id is not None:
            await self._access.require_read(deck_id, user_id)
        return await self._progress.list_for_user(
            user_id, deck_id=deck_id, limit=limit, offset=offset
        )

    async def get_readable(self, word_id: UUID, user_id: UUID) -> StudiedWord:
        """The card plus this learner's progress, if they are in its deck.

        404 rather than 403 for a non-member: a stranger walking word ids must
        not be able to learn which ones exist.
        """
        studied = await self._progress.get_for_user(word_id, user_id)
        if studied is None:
            raise NotFoundError("Word not found.")
        return studied

    async def create(
        self,
        user_id: UUID,
        *,
        deck_id: UUID,
        term: str,
        meaning: str,
        example: str | None,
        sense_label: str | None,
        definition: str | None = None,
    ) -> StudiedWord:
        await self._access.require_edit_words(deck_id, user_id)
        word = Word(
            created_by_user_id=user_id,
            deck_id=deck_id,
            term=term.strip(),
            meaning=meaning.strip(),
            definition=(definition or "").strip() or None,
            example=(example or "").strip() or None,
            sense_label=sense_label,
        )
        created = await self._words.add(word)
        # No progress row is written: the card is new to everyone, and an
        # unstudied word already reads as box 1, due now.
        return await self.get_readable(created.id, user_id)

    async def update(
        self,
        word_id: UUID,
        user_id: UUID,
        *,
        term: str | None = None,
        meaning: str | None = None,
        definition: str | None = None,
        example: str | None = None,
        sense_label: str | None = None,
        deck_id: UUID | None = None,
    ) -> StudiedWord:
        studied = await self.get_readable(word_id, user_id)
        await self._access.require_edit_words(studied.deck_id, user_id)
        word = studied.word
        if deck_id is not None and deck_id != word.deck_id:
            # Both ends: you must be allowed to take it out of where it is and
            # to put it where it is going.
            await self._access.require_edit_words(deck_id, user_id)
            word.deck_id = deck_id
        if term is not None:
            word.term = term.strip()
        if meaning is not None:
            word.meaning = meaning.strip()
        # Clearing is expressed as "" — an omitted field leaves the definition
        # alone, which is what keeps a client that predates it from wiping one.
        if definition is not None:
            word.definition = definition.strip() or None
        if example is not None:
            word.example = example.strip() or None
        if sense_label is not None:
            word.sense_label = sense_label
        word.updated_at = datetime.now(UTC)
        await self._words.update(word)
        return await self.get_readable(word_id, user_id)

    async def delete(self, word_id: UUID, user_id: UUID) -> None:
        studied = await self.get_readable(word_id, user_id)
        await self._access.require_edit_words(studied.deck_id, user_id)
        # Everyone's progress on the card goes with it, by cascade: the card is
        # gone, so nobody's boxes against it mean anything any more.
        await self._words.delete(word_id)
