"""SQLAlchemy implementation of :class:`DeckRepository`."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.deck import Deck
from app.domain.repositories.deck_repository import DeckRepository
from app.infrastructure.db import mappers
from app.infrastructure.db.models.deck import DeckModel
from app.infrastructure.db.models.deck_member import DeckMemberModel


class SqlAlchemyDeckRepository(DeckRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, deck_id: UUID) -> Deck | None:
        model = await self._session.get(DeckModel, deck_id)
        return mappers.deck_to_entity(model) if model else None

    async def list_for_user(self, user_id: UUID) -> list[Deck]:
        # Membership, not decks.user_id: "my decks" now includes decks shared
        # with me, and the owner is a member like anyone else. decks.user_id is
        # creator attribution and is never an access check.
        stmt = (
            select(DeckModel)
            .join(DeckMemberModel, DeckMemberModel.deck_id == DeckModel.id)
            .where(DeckMemberModel.user_id == user_id)
            .order_by(DeckModel.created_at.asc())
        )
        models = (await self._session.execute(stmt)).scalars().all()
        return [mappers.deck_to_entity(m) for m in models]

    async def add(self, deck: Deck) -> Deck:
        model = DeckModel(id=deck.id)
        mappers.apply_deck(deck, model)
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return mappers.deck_to_entity(model)

    async def update(self, deck: Deck) -> Deck:
        model = await self._session.get(DeckModel, deck.id)
        if model is None:
            raise ValueError(f"Deck {deck.id} does not exist")
        mappers.apply_deck(deck, model)
        await self._session.flush()
        await self._session.refresh(model)
        return mappers.deck_to_entity(model)

    async def delete(self, deck_id: UUID) -> None:
        await self._session.execute(delete(DeckModel).where(DeckModel.id == deck_id))
