"""Port: the recency list of people a learner has shared with."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class FriendView:
    username: str
    name: str
    last_shared_at: datetime | None


class FriendRepository(ABC):
    @abstractmethod
    async def list_for_user(self, user_id: UUID) -> list[FriendView]:
        """Most-recently-shared first; never-shared last."""

    @abstractmethod
    async def link(
        self, user_id: UUID, friend_user_id: UUID, *, shared_at: datetime | None = None
    ) -> None:
        """Add or refresh a link. Idempotent — adding twice is not an error.

        ``shared_at`` is set when this follows an actual share, which is what
        moves the person up the list.
        """

    @abstractmethod
    async def unlink(self, user_id: UUID, friend_user_id: UUID) -> None: ...
