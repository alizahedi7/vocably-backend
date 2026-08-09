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
        """Exact match only, on the already-lowercased handle."""

    @abstractmethod
    async def search_by_username(
        self, prefix: str, *, exclude_user_id: UUID, limit: int
    ) -> list[User]:
        """Handles beginning with ``prefix``, best match first.

        A **prefix** and nothing else — not a substring, not the display name.
        Searching names would make a person findable by something they never
        chose to be addressed by; a handle is the one string in this product a
        learner picks *so that* other people can type it, and typing the first
        few characters of one you were told is the whole use case. Anyone
        wanting to be unfindable can hold a handle nobody would guess at.

        Ordered shortest-first so an exact match leads and the near-misses
        follow it, then alphabetically so the list never jitters between calls.
        ``limit`` is applied in SQL: the caller shows a handful, and a prefix
        like ``a`` must not drag the table through Python to find them.
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
