"""Port: the invite link a deck hands out."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.entities.deck_invite import DeckInvite


class DeckInviteRepository(ABC):
    @abstractmethod
    async def get_for_deck(self, deck_id: UUID) -> DeckInvite | None: ...

    @abstractmethod
    async def get_by_code(self, code: str) -> DeckInvite | None:
        """Resolve a bearer code. Exact match; codes are stored uppercased."""

    @abstractmethod
    async def upsert(self, invite: DeckInvite) -> DeckInvite:
        """Create or reopen the deck's single invite row, keeping its code."""
