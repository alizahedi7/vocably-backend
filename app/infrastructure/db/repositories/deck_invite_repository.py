"""SQLAlchemy implementation of :class:`DeckInviteRepository`."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.deck_invite import DeckInvite
from app.domain.enums import DeckRole
from app.domain.repositories.deck_invite_repository import DeckInviteRepository
from app.infrastructure.db.dialects import upsert_insert
from app.infrastructure.db.models.deck_invite import DeckInviteModel


def _to_entity(m: DeckInviteModel) -> DeckInvite:
    return DeckInvite(
        deck_id=m.deck_id,
        code=m.code,
        role=DeckRole.parse(m.role),
        is_open=m.is_open,
        created_by_user_id=m.created_by_user_id,
        expires_at=m.expires_at,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


class SqlAlchemyDeckInviteRepository(DeckInviteRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_deck(self, deck_id: UUID) -> DeckInvite | None:
        model = await self._session.get(DeckInviteModel, deck_id)
        return _to_entity(model) if model else None

    async def get_by_code(self, code: str) -> DeckInvite | None:
        stmt = select(DeckInviteModel).where(DeckInviteModel.code == code)
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_entity(model) if model else None

    async def upsert(self, invite: DeckInvite) -> DeckInvite:
        # One statement, because read-then-insert races: two taps on "share"
        # both see no row and the second violates the primary key. Note what
        # the conflict branch does *not* set — ``code``. A link already handed
        # to a class must keep working when the owner reopens it, so reopening
        # never mints a new one.
        stmt = (
            upsert_insert(self._session)(DeckInviteModel)
            .values(
                deck_id=invite.deck_id,
                code=invite.code,
                role=invite.role.value,
                is_open=invite.is_open,
                created_by_user_id=invite.created_by_user_id,
                expires_at=invite.expires_at,
            )
            .on_conflict_do_update(
                index_elements=["deck_id"],
                set_={
                    "role": invite.role.value,
                    "is_open": invite.is_open,
                    "expires_at": invite.expires_at,
                },
            )
            .returning(*DeckInviteModel.__table__.columns)
        )
        row = (await self._session.execute(stmt)).mappings().one()
        return DeckInvite(
            deck_id=row["deck_id"],
            code=row["code"],
            role=DeckRole.parse(row["role"]),
            is_open=row["is_open"],
            created_by_user_id=row["created_by_user_id"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
