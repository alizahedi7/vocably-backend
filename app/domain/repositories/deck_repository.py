"""Port: persistence contract for :class:`~app.domain.entities.deck.Deck`."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from uuid import UUID

from app.domain.entities.deck import Deck
from app.domain.enums import DeckRole


class DeckRepository(ABC):
    @abstractmethod
    async def get(self, deck_id: UUID) -> Deck | None: ...

    @abstractmethod
    async def list_for_user(self, user_id: UUID) -> list[Deck]: ...

    @abstractmethod
    async def list_for_user_with_role(self, user_id: UUID) -> list[tuple[Deck, DeckRole]]: ...

    @abstractmethod
    async def add(self, deck: Deck) -> Deck: ...

    @abstractmethod
    async def update(self, deck: Deck) -> Deck: ...

    @abstractmethod
    async def delete(self, deck_id: UUID) -> None: ...

    @abstractmethod
    async def delete_many(self, deck_ids: Sequence[UUID]) -> None: ...
