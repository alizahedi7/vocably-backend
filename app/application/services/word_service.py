"""Word (flashcard) use cases with ownership enforcement."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.domain.entities.word import Word
from app.domain.repositories.deck_repository import DeckRepository
from app.domain.repositories.word_repository import WordRepository


class WordService:
    def __init__(self, words: WordRepository, decks: DeckRepository) -> None:
        self._words = words
        self._decks = decks

    async def list_words(
        self,
        user_id: UUID,
        *,
        deck_id: UUID | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Word]:
        if deck_id is not None:
            await self._assert_owns_deck(deck_id, user_id)
        return await self._words.list_for_user(
            user_id, deck_id=deck_id, limit=limit, offset=offset
        )

    async def get_owned(self, word_id: UUID, user_id: UUID) -> Word:
        word = await self._words.get(word_id)
        if word is None:
            raise NotFoundError("Word not found.")
        if word.user_id != user_id:
            raise PermissionDeniedError("This word belongs to another user.")
        return word

    async def create(
        self,
        user_id: UUID,
        *,
        deck_id: UUID,
        term: str,
        meaning: str,
        example: str | None,
        sense_label: str | None,
    ) -> Word:
        await self._assert_owns_deck(deck_id, user_id)
        word = Word(
            user_id=user_id,
            deck_id=deck_id,
            term=term.strip(),
            meaning=meaning.strip(),
            example=(example or "").strip() or None,
            sense_label=sense_label,
        )
        return await self._words.add(word)

    async def update(
        self,
        word_id: UUID,
        user_id: UUID,
        *,
        term: str | None = None,
        meaning: str | None = None,
        example: str | None = None,
        sense_label: str | None = None,
        deck_id: UUID | None = None,
    ) -> Word:
        word = await self.get_owned(word_id, user_id)
        if deck_id is not None and deck_id != word.deck_id:
            await self._assert_owns_deck(deck_id, user_id)
            word.deck_id = deck_id
        if term is not None:
            word.term = term.strip()
        if meaning is not None:
            word.meaning = meaning.strip()
        if example is not None:
            word.example = example.strip() or None
        if sense_label is not None:
            word.sense_label = sense_label
        word.updated_at = datetime.now(UTC)
        return await self._words.update(word)

    async def delete(self, word_id: UUID, user_id: UUID) -> None:
        await self.get_owned(word_id, user_id)
        await self._words.delete(word_id)

    async def _assert_owns_deck(self, deck_id: UUID, user_id: UUID) -> None:
        deck = await self._decks.get(deck_id)
        if deck is None:
            raise NotFoundError("Deck not found.")
        if deck.user_id != user_id:
            raise PermissionDeniedError("This deck belongs to another user.")
