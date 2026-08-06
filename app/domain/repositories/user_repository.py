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
    async def get_by_username(self, username: str) -> User | None:
        """Exact match only, on the already-lowercased handle.

        Deliberately not a prefix or fuzzy search: a "find people" endpoint is a
        product decision with a consent question attached, not a convenience.
        """

    @abstractmethod
    async def username_taken(self, username: str) -> bool: ...

    @abstractmethod
    async def add(self, user: User) -> User: ...

    @abstractmethod
    async def update(self, user: User) -> User: ...
