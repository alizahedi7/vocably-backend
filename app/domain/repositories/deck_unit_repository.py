"""Port: persistence for units/lessons inside a deck."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.entities.deck_unit import DeckUnit


class DeckUnitRepository(ABC):
    @abstractmethod
    async def get(self, unit_id: UUID) -> DeckUnit | None: ...

    @abstractmethod
    async def list_for_deck(self, deck_id: UUID) -> list[DeckUnit]:
        """Ordered by ``position`` — never by name; "Unit 10" sorts before 2."""

    @abstractmethod
    async def next_position(self, deck_id: UUID) -> int:
        """``max(position) + 1`` for the deck, or 0 when it has no units."""

    @abstractmethod
    async def add(self, unit: DeckUnit) -> DeckUnit: ...

    @abstractmethod
    async def update(self, unit: DeckUnit) -> DeckUnit: ...

    @abstractmethod
    async def delete(self, unit_id: UUID) -> int:
        """Delete the unit, returning how many cards came loose.

        The count is the product surface: the client shows "N words moved back
        to the deck" and asks for no confirmation, precisely because nothing is
        lost — the cards fall back into the deck via ``ON DELETE SET NULL``.
        """
