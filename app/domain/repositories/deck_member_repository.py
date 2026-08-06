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
    async def add_if_absent(self, member: DeckMember) -> bool:
        """Insert a membership, reporting whether it was actually created.

        Check-then-insert races: two taps on an invite link arrive together,
        both see no row, and the second insert violates the primary key. This
        does the check in the same statement, so joining twice is quiet rather
        than a 500.
        """

    @abstractmethod
    async def update(self, member: DeckMember) -> DeckMember: ...

    @abstractmethod
    async def remove(self, deck_id: UUID, user_id: UUID) -> None: ...

    @abstractmethod
    async def owned_deck_ids(self, user_id: UUID) -> list[UUID]:
        """Decks this user owns, shared or not."""

    @abstractmethod
    async def shared_deck_names_owned_by(self, user_id: UUID) -> list[str]:
        """Names of decks this user owns that somebody else is also in.

        The account-deletion guard: erasing their owner would take a class's
        vocabulary with it, so deletion is refused until they are handed over
        or deleted deliberately.
        """
