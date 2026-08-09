"""SQLAlchemy implementation of :class:`UserRepository`."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError
from app.domain.entities.user import User
from app.domain.repositories.user_repository import UserRepository
from app.infrastructure.db import mappers
from app.infrastructure.db.models.user import UserModel


class SqlAlchemyUserRepository(UserRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: UUID) -> User | None:
        model = await self._session.get(UserModel, user_id)
        return mappers.user_to_entity(model) if model else None

    async def get_by_phone(self, phone: str) -> User | None:
        stmt = select(UserModel).where(UserModel.phone == phone)
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return mappers.user_to_entity(model) if model else None

    async def get_by_google_sub(self, google_sub: str) -> User | None:
        stmt = select(UserModel).where(UserModel.google_sub == google_sub)
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return mappers.user_to_entity(model) if model else None

    async def list_by_ids(self, user_ids: Sequence[UUID]) -> dict[UUID, User]:
        if not user_ids:
            return {}
        stmt = select(UserModel).where(UserModel.id.in_(list(user_ids)))
        models = (await self._session.execute(stmt)).scalars().all()
        return {m.id: mappers.user_to_entity(m) for m in models}

    async def delete(self, user_id: UUID) -> None:
        await self._session.execute(delete(UserModel).where(UserModel.id == user_id))

    async def get_by_username(self, username: str) -> User | None:
        stmt = select(UserModel).where(UserModel.username == username)
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return mappers.user_to_entity(model) if model else None

    async def search_by_username(
        self, prefix: str, *, exclude_user_id: UUID, limit: int
    ) -> list[User]:
        # ``startswith`` with autoescape, so a handle containing ``_`` — which
        # is legal, and a LIKE wildcard — matches itself rather than any
        # character. The unique index on ``username`` serves the prefix range.
        stmt = (
            select(UserModel)
            .where(
                UserModel.username.is_not(None),
                UserModel.username.startswith(prefix, autoescape=True),
                UserModel.id != exclude_user_id,
            )
            .order_by(func.length(UserModel.username), UserModel.username)
            .limit(limit)
        )
        models = (await self._session.execute(stmt)).scalars().all()
        return [mappers.user_to_entity(m) for m in models]

    async def username_taken(self, username: str) -> bool:
        stmt = select(UserModel.id).where(UserModel.username == username).limit(1)
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def add(self, user: User) -> User:
        model = UserModel(id=user.id)
        mappers.apply_user(user, model)
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return mappers.user_to_entity(model)

    async def update(self, user: User) -> User:
        model = await self._session.get(UserModel, user.id)
        if model is None:
            raise ValueError(f"User {user.id} does not exist")
        mappers.apply_user(user, model)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # The unique index is the real arbiter of a handle, not the
            # availability check before it: two people can pass that check in
            # the same instant and only one row can win. Translating here keeps
            # the loser on a 409 with copy they can read, rather than a 500.
            if _is_username_conflict(exc):
                raise AlreadyExistsError("That handle is already taken.") from exc
            raise
        await self._session.refresh(model)
        return mappers.user_to_entity(model)


def _is_username_conflict(exc: IntegrityError) -> bool:
    """Whether this violation is the handle's unique index.

    Matched on the text because the constraint is named differently by each
    dialect, and a user row has several unique columns — a phone or google_sub
    conflict must never be reported to someone as a taken handle.
    """
    return "username" in str(exc.orig).lower()
