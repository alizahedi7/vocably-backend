"""SQLAlchemy implementation of :class:`FriendRepository`."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.repositories.friend_repository import (
    FriendRepository,
    FriendRequestView,
    FriendView,
)
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
            .where(
                FriendLinkModel.user_id == user_id,
                # A request nobody has answered is not a friend. Listing one
                # would tell the sender they had added somebody who has not
                # agreed to it.
                FriendLinkModel.accepted.is_(True),
            )
            # NULLS LAST: someone added by hand and never shared with sits at
            # the bottom, which is what the client's own ordering does.
            .order_by(FriendLinkModel.last_shared_at.desc().nullslast(), UserModel.username)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            FriendView(username=username or "", name=name, last_shared_at=last_shared_at)
            for username, name, last_shared_at in rows
        ]

    async def list_requests_for(self, user_id: UUID) -> list[FriendRequestView]:
        stmt = (
            select(UserModel.username, UserModel.name, FriendLinkModel.created_at)
            .join(UserModel, UserModel.id == FriendLinkModel.user_id)
            .where(
                FriendLinkModel.friend_user_id == user_id,
                FriendLinkModel.accepted.is_(False),
            )
            .order_by(FriendLinkModel.created_at.desc(), UserModel.username)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            FriendRequestView(username=username or "", name=name, requested_at=requested_at)
            for username, name, requested_at in rows
        ]

    async def request(self, user_id: UUID, friend_user_id: UUID, *, at: datetime) -> None:
        stmt = (
            upsert_insert(self._session)(FriendLinkModel)
            .values(
                user_id=user_id,
                friend_user_id=friend_user_id,
                accepted=False,
                created_at=at,
                updated_at=at,
            )
            # Never touches `accepted`. Asking again while a request is out
            # refreshes nothing that matters and, more importantly, asking
            # somebody who is *already* a friend cannot demote the friendship
            # back to a question — which a stale client will do, since its list
            # can be a poll behind.
            .on_conflict_do_nothing(index_elements=["user_id", "friend_user_id"])
        )
        await self._session.execute(stmt)

    async def link(
        self, user_id: UUID, friend_user_id: UUID, *, shared_at: datetime | None = None
    ) -> None:
        values: dict[str, object] = {
            "user_id": user_id,
            "friend_user_id": friend_user_id,
            "accepted": True,
        }
        if shared_at is not None:
            values["last_shared_at"] = shared_at
        insert = upsert_insert(self._session)(FriendLinkModel).values(**values)
        # Sharing with somebody settles an open request in the same direction:
        # you have just sent them a deck, which is a stronger statement than the
        # one you were waiting on them to answer.
        changes: dict[str, object] = {"accepted": True}
        if shared_at is not None:
            changes["last_shared_at"] = shared_at
        stmt = insert.on_conflict_do_update(
            index_elements=["user_id", "friend_user_id"],
            set_=changes,
        )
        await self._session.execute(stmt)

    async def accept(self, user_id: UUID, requester_id: UUID, *, at: datetime) -> bool:
        # RETURNING rather than `rowcount`: it is the typed answer to "was
        # there a row?", and it makes the check part of the same statement.
        answered = (
            await self._session.execute(
                update(FriendLinkModel)
                .where(
                    FriendLinkModel.user_id == requester_id,
                    FriendLinkModel.friend_user_id == user_id,
                    FriendLinkModel.accepted.is_(False),
                )
                .values(accepted=True, updated_at=at)
                .returning(FriendLinkModel.user_id)
            )
        ).first()
        if answered is None:
            return False
        # The other half. Agreeing to be added is agreeing to know each other,
        # so the accepter holds the link too — without this, only the person who
        # asked would have anyone in their list.
        await self.link(user_id, requester_id)
        return True

    async def unlink(self, user_id: UUID, friend_user_id: UUID) -> None:
        # Both directions. Half a removed friendship is somebody still holding
        # you on a list you are no longer on, which is the worse of the two
        # states to leave behind.
        await self._session.execute(
            delete(FriendLinkModel).where(
                or_(
                    (FriendLinkModel.user_id == user_id)
                    & (FriendLinkModel.friend_user_id == friend_user_id),
                    (FriendLinkModel.user_id == friend_user_id)
                    & (FriendLinkModel.friend_user_id == user_id),
                )
            )
        )

    async def decline(self, user_id: UUID, requester_id: UUID) -> None:
        await self._session.execute(
            delete(FriendLinkModel).where(
                FriendLinkModel.user_id == requester_id,
                FriendLinkModel.friend_user_id == user_id,
                # Only an unanswered request. A friendship is ended by
                # `unlink`, and letting this delete one would make "decline"
                # able to quietly remove somebody already accepted.
                FriendLinkModel.accepted.is_(False),
            )
        )
