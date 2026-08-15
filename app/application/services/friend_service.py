"""The people a learner knows, and the people who have asked to know them.

Adding somebody used to be the whole transaction: one row, one direction, no
consent, on the reasoning that it revealed nothing the sharer did not already
know because they had typed the handle themselves. That was true of a recency
list built out of deck shares. It stopped being true when the app grew a people
search — a handle is *found* now — and it was never true of the other person,
who was not told, could not refuse, and could not see it had happened.

So adding is an **offer**, answered by its recipient, exactly as a shared deck
is. The one path that still writes a friendship outright is sharing a deck,
which is a stronger act than the request it replaces and which the recipient
answers on its own terms.

**Handle lookup stays exact-match here.** The people search is its own endpoint,
rate-limited and prefix-matching handles only; this one resolves a handle
somebody already has.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.core.exceptions import NotFoundError, ValidationError
from app.domain.entities.user import User
from app.domain.repositories.friend_repository import (
    FriendRepository,
    FriendRequestView,
    FriendView,
)
from app.domain.repositories.user_repository import UserRepository


class FriendService:
    def __init__(self, friends: FriendRepository, users: UserRepository) -> None:
        self._friends = friends
        self._users = users

    async def list_friends(self, user_id: UUID) -> list[FriendView]:
        return await self._friends.list_for_user(user_id)

    async def list_requests(self, user_id: UUID) -> list[FriendRequestView]:
        """Who has asked to add this learner and is waiting on an answer."""
        return await self._friends.list_requests_for(user_id)

    async def add(self, user_id: UUID, *, username: str) -> FriendRequestView:
        """Ask to add somebody. Nothing happens to their list until they agree.

        Idempotent — asking twice is not an error, and asking somebody who is
        already a friend changes nothing rather than reopening a settled
        question. Nothing beyond their name is revealed either way: no phone,
        no email, no stats, and in particular no signal about whether they have
        seen the request, because "did they ignore me?" is not a question this
        product answers.
        """
        friend = await self._resolve(username, actor_id=user_id)
        now = datetime.now(UTC)
        await self._friends.request(user_id, friend.id, at=now)
        return FriendRequestView(
            username=friend.username or username.strip().lower(),
            name=friend.name,
            requested_at=now,
        )

    async def accept(self, user_id: UUID, *, username: str) -> FriendView:
        """Agree to a request. This is what makes the two people friends.

        The link is written in both directions, so neither of them ends up
        holding somebody who does not hold them.
        """
        requester = await self._resolve(username, actor_id=user_id)
        now = datetime.now(UTC)
        if not await self._friends.accept(user_id, requester.id, at=now):
            # 404 rather than a conflict: from here, a request that was declined
            # a moment ago, one that never existed, and one addressed to
            # somebody else are the same answer — and must be, or this becomes a
            # way to ask who has been asking whom.
            raise NotFoundError("No request from that handle")
        return FriendView(
            username=requester.username or username.strip().lower(),
            name=requester.name,
            last_shared_at=None,
        )

    async def decline(self, user_id: UUID, *, username: str) -> None:
        """Refuse a request. The sender is not told, and can ask again.

        Silent by design, and the same rule the deck share follows: a list of
        who turned you down is a different product from a way to reach people.
        Declining a request that is not there is not an error — the answer the
        caller wanted is true either way.
        """
        requester = await self._users.get_by_username(username.strip().lower())
        if requester is None:
            raise NotFoundError("No one uses that handle")
        await self._friends.decline(user_id, requester.id)

    async def remove(self, user_id: UUID, *, username: str) -> None:
        """Remove somebody, from both lists. Also the way to undo a request."""
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
