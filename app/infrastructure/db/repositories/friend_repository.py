"""SQLAlchemy implementation of :class:`FriendRepository`."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.repositories.friend_repository import FriendRepository, FriendView
from app.infrastructure.db.dialects import upsert_insert
from app.infrastructure.db.models.friend_link import FriendLinkModel
from app.infrastructure.db.models.user import UserModel


class SqlAlchemyFriendRepository(FriendRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_user(self, user_id: UUID) -> list[FriendView]:
        stmt = (
            select(UserModel.username, UserModel.name, FriendLinkModel.last_shared_at)
            .join(UserModel, UserModel.id == FriendLinkModel.friend_user_id)
            .where(FriendLinkModel.user_id == user_id)
            # NULLS LAST: someone added by hand and never shared with sits at
            # the bottom, which is what the client's own ordering does.
            .order_by(FriendLinkModel.last_shared_at.desc().nullslast(), UserModel.username)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            FriendView(username=username or "", name=name, last_shared_at=last_shared_at)
            for username, name, last_shared_at in rows
        ]

    async def link(
        self, user_id: UUID, friend_user_id: UUID, *, shared_at: datetime | None = None
    ) -> None:
        values: dict[str, object] = {"user_id": user_id, "friend_user_id": friend_user_id}
        if shared_at is not None:
            values["last_shared_at"] = shared_at
        insert = upsert_insert(self._session)(FriendLinkModel).values(**values)
        if shared_at is None:
            # Adding by hand must not clear a share time already recorded.
            stmt = insert.on_conflict_do_nothing(index_elements=["user_id", "friend_user_id"])
        else:
            stmt = insert.on_conflict_do_update(
                index_elements=["user_id", "friend_user_id"],
                set_={"last_shared_at": shared_at},
            )
        await self._session.execute(stmt)

    async def unlink(self, user_id: UUID, friend_user_id: UUID) -> None:
        await self._session.execute(
            delete(FriendLinkModel).where(
                FriendLinkModel.user_id == user_id,
                FriendLinkModel.friend_user_id == friend_user_id,
            )
        )
