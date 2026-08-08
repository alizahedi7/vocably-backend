"""Port: persistence contract for :class:`~app.domain.entities.user.User`."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
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
    async def list_by_ids(self, user_ids: Sequence[UUID]) -> dict[UUID, User]:
        """Fetch several users at once, keyed by id.

        Exists so the roster does not issue one query per member — thirty
        students would otherwise be thirty round trips behind a single screen.
        """

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

    @abstractmethod
    async def delete(self, user_id: UUID) -> None:
        """Erase the account.

        Everything keyed on the user cascades: their progress, their review
        history, their memberships, their daily activity. Cards they wrote in
        *other people's* decks survive, uncredited. Callers must delete the
        decks the user owns first — see ``UserService.delete_account``.
        """
