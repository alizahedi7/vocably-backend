"""Port: persistence contract for :class:`~app.domain.entities.user.User`."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.entities.user import User


class UserRepository(ABC):
    @abstractmethod
    async def get(self, user_id: UUID) -> User | None: ...

    @abstractmethod
    async def get_by_phone(self, phone: str) -> User | None: ...

    @abstractmethod
    async def get_by_google_sub(self, google_sub: str) -> User | None: ...

    @abstractmethod
    async def add(self, user: User) -> User: ...

    @abstractmethod
    async def update(self, user: User) -> User: ...
