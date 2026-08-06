"""Port: who belongs to a deck, and in what role."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.entities.deck_member import DeckMember


class DeckMemberRepository(ABC):
    @abstractmethod
    async def get(self, deck_id: UUID, user_id: UUID) -> DeckMember | None:
        """This user's membership of this deck, or ``None`` if they have none.

        The single authorization question every deck- and word-scoped route
        asks. ``None`` means "not a member", which callers translate to 404 —
        never 403, which would confirm the deck exists.
        """

    @abstractmethod
    async def list_for_deck(self, deck_id: UUID) -> list[DeckMember]: ...

    @abstractmethod
    async def add(self, member: DeckMember) -> DeckMember: ...

    @abstractmethod
    async def update(self, member: DeckMember) -> DeckMember: ...

    @abstractmethod
    async def remove(self, deck_id: UUID, user_id: UUID) -> None: ...
