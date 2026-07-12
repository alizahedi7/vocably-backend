"""Deck use cases with ownership enforcement."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.application.dto import DeckView
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.domain.entities.deck import Deck
from app.domain.enums import LeitnerBox
from app.domain.repositories.deck_repository import DeckRepository
from app.domain.repositories.word_repository import WordRepository

_MAX_BOX = int(LeitnerBox.MASTERED)


class DeckService:
    def __init__(self, decks: DeckRepository, words: WordRepository) -> None:
        self._decks = decks
        self._words = words

    async def list_decks(self, user_id: UUID) -> list[Deck]:
        return await self._decks.list_for_user(user_id)

    async def list_with_stats(self, user_id: UUID) -> list[DeckView]:
        """Decks enriched with word/due counts and a progress percentage."""
        decks = await self._decks.list_for_user(user_id)
        totals = await self._words.deck_totals(user_id)
        due = await self._words.deck_due_counts(user_id, datetime.now(UTC))

        views: list[DeckView] = []
        for deck in decks:
            count, box_sum = totals.get(deck.id, (0, 0))
            progress = round((box_sum / (count * _MAX_BOX)) * 100) if count else 0
            views.append(
                DeckView(
                    deck=deck,
                    word_count=count,
                    due_count=due.get(deck.id, 0),
                    progress_pct=progress,
                )
            )
        return views

    async def get_owned(self, deck_id: UUID, user_id: UUID) -> Deck:
        deck = await self._decks.get(deck_id)
        if deck is None:
            raise NotFoundError("Deck not found.")
        if deck.user_id != user_id:
            raise PermissionDeniedError("This deck belongs to another user.")
        return deck

    async def create(self, user_id: UUID, *, name: str, hue: int) -> Deck:
        deck = Deck(user_id=user_id, name=name.strip(), hue=hue)
        return await self._decks.add(deck)

    async def update(
        self,
        deck_id: UUID,
        user_id: UUID,
        *,
        name: str | None = None,
        hue: int | None = None,
    ) -> Deck:
        deck = await self.get_owned(deck_id, user_id)
        if name is not None:
            deck.name = name.strip()
        if hue is not None:
            deck.hue = hue
        deck.updated_at = datetime.now(UTC)
        return await self._decks.update(deck)

    async def delete(self, deck_id: UUID, user_id: UUID) -> None:
        await self.get_owned(deck_id, user_id)  # ownership check
        await self._decks.delete(deck_id)
