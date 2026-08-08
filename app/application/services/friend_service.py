"""The people a learner shares with, so a handle is typed once.

Deliberately small: a recency list, not a social graph. One-directional and
needing no consent, because it reveals nothing the sharer did not already know
— they typed the handle. It must stay that way: **handle lookup is exact-match
only.** No prefix search, no "find people" endpoint. Adding one is a product
decision with a consent question attached, not a convenience.
"""

from __future__ import annotations

from uuid import UUID

from app.core.exceptions import NotFoundError, ValidationError
from app.domain.entities.user import User
from app.domain.repositories.friend_repository import FriendRepository, FriendView
from app.domain.repositories.user_repository import UserRepository


class FriendService:
    def __init__(self, friends: FriendRepository, users: UserRepository) -> None:
        self._friends = friends
        self._users = users

    async def list_friends(self, user_id: UUID) -> list[FriendView]:
        return await self._friends.list_for_user(user_id)

    async def add(self, user_id: UUID, *, username: str) -> FriendView:
        """Add someone by handle. Idempotent — adding twice is not an error.

        Nothing beyond their name is revealed: no phone, no email, no stats.
        """
        friend = await self._resolve(username, actor_id=user_id)
        await self._friends.link(user_id, friend.id)
        return FriendView(
            username=friend.username or username.strip().lower(),
            name=friend.name,
            # Freshly added by hand, never shared with. The client sorts these
            # last, which is the honest place for them.
            last_shared_at=None,
        )

    async def remove(self, user_id: UUID, *, username: str) -> None:
        friend = await self._users.get_by_username(username.strip().lower())
        if friend is None:
            raise NotFoundError("No one uses that handle")
        await self._friends.unlink(user_id, friend.id)

    async def _resolve(self, username: str, *, actor_id: UUID) -> User:
        handle = username.strip().lower()
        if not handle:
            raise ValidationError("Enter a handle first")
        friend = await self._users.get_by_username(handle)
        if friend is None:
            raise ValidationError("No one uses that handle")
        if friend.id == actor_id:
            raise ValidationError("That is your own handle")
        return friend
