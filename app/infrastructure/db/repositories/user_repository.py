"""SQLAlchemy implementation of :class:`UserRepository`."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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

    async def get_by_username(self, username: str) -> User | None:
        stmt = select(UserModel).where(UserModel.username == username)
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return mappers.user_to_entity(model) if model else None

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
        await self._session.flush()
        await self._session.refresh(model)
        return mappers.user_to_entity(model)
