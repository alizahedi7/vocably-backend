"""SQLAlchemy implementation of :class:`DeckMemberRepository`."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.deck_member import DeckMember
from app.domain.enums import DeckRole
from app.domain.repositories.deck_member_repository import DeckMemberRepository
from app.infrastructure.db import mappers
from app.infrastructure.db.dialects import upsert_insert
from app.infrastructure.db.models.deck import DeckModel
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
            self_paced=member.self_paced,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return mappers.deck_member_to_entity(model)

    async def add_if_absent(self, member: DeckMember) -> bool:
        stmt = (
            upsert_insert(self._session)(DeckMemberModel)
            .values(
                deck_id=member.deck_id,
                user_id=member.user_id,
                role=member.role.value,
                invited_by_user_id=member.invited_by_user_id,
                joined_at=member.joined_at,
                self_paced=member.self_paced,
            )
            # DO NOTHING, not DO UPDATE: someone already in the deck keeps the
            # role and join date they have. A second tap on a link must not
            # silently demote a member the owner has since promoted.
            .on_conflict_do_nothing(index_elements=["deck_id", "user_id"])
            # RETURNING is how "did it insert?" is answered portably: DO
            # NOTHING returns no row when it skipped, on both dialects.
            .returning(DeckMemberModel.user_id)
        )
        inserted = (await self._session.execute(stmt)).scalar_one_or_none()
        return inserted is not None

    async def update(self, member: DeckMember) -> DeckMember:
        model = await self._session.get(DeckMemberModel, (member.deck_id, member.user_id))
        if model is None:
            raise ValueError(f"User {member.user_id} is not a member of deck {member.deck_id}")
        model.role = member.role.value
        await self._session.flush()
        await self._session.refresh(model)
        return mappers.deck_member_to_entity(model)

    async def self_paced_deck_ids(self, user_id: UUID) -> set[UUID]:
        stmt = select(DeckMemberModel.deck_id).where(
            DeckMemberModel.user_id == user_id,
            DeckMemberModel.self_paced.is_(True),
        )
        return set((await self._session.execute(stmt)).scalars().all())

    async def owned_deck_ids(self, user_id: UUID) -> list[UUID]:
        stmt = select(DeckMemberModel.deck_id).where(
            DeckMemberModel.user_id == user_id,
            DeckMemberModel.role == DeckRole.OWNER.value,
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def shared_deck_names_owned_by(self, user_id: UUID) -> list[str]:
        others = (
            select(DeckMemberModel.deck_id).where(DeckMemberModel.user_id != user_id).subquery()
        )
        stmt = (
            select(DeckModel.name)
            .join(DeckMemberModel, DeckMemberModel.deck_id == DeckModel.id)
            .where(
                DeckMemberModel.user_id == user_id,
                DeckMemberModel.role == DeckRole.OWNER.value,
                DeckModel.id.in_(select(others.c.deck_id)),
            )
            .order_by(DeckModel.name)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def remove(self, deck_id: UUID, user_id: UUID) -> None:
        await self._session.execute(
            delete(DeckMemberModel).where(
                DeckMemberModel.deck_id == deck_id,
                DeckMemberModel.user_id == user_id,
            )
        )
