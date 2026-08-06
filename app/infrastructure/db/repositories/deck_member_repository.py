"""SQLAlchemy implementation of :class:`DeckMemberRepository`."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.deck_member import DeckMember
from app.domain.repositories.deck_member_repository import DeckMemberRepository
from app.infrastructure.db import mappers
from app.infrastructure.db.models.deck_member import DeckMemberModel


class SqlAlchemyDeckMemberRepository(DeckMemberRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, deck_id: UUID, user_id: UUID) -> DeckMember | None:
        model = await self._session.get(DeckMemberModel, (deck_id, user_id))
        return mappers.deck_member_to_entity(model) if model else None

    async def list_for_deck(self, deck_id: UUID) -> list[DeckMember]:
        stmt = (
            select(DeckMemberModel)
            .where(DeckMemberModel.deck_id == deck_id)
            .order_by(DeckMemberModel.joined_at.asc())
        )
        models = (await self._session.execute(stmt)).scalars().all()
        return [mappers.deck_member_to_entity(m) for m in models]

    async def add(self, member: DeckMember) -> DeckMember:
        model = DeckMemberModel(
            deck_id=member.deck_id,
            user_id=member.user_id,
            role=member.role.value,
            invited_by_user_id=member.invited_by_user_id,
            joined_at=member.joined_at,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return mappers.deck_member_to_entity(model)

    async def update(self, member: DeckMember) -> DeckMember:
        model = await self._session.get(DeckMemberModel, (member.deck_id, member.user_id))
        if model is None:
            raise ValueError(f"User {member.user_id} is not a member of deck {member.deck_id}")
        model.role = member.role.value
        await self._session.flush()
        await self._session.refresh(model)
        return mappers.deck_member_to_entity(model)

    async def remove(self, deck_id: UUID, user_id: UUID) -> None:
        await self._session.execute(
            delete(DeckMemberModel).where(
                DeckMemberModel.deck_id == deck_id,
                DeckMemberModel.user_id == user_id,
            )
        )
