"""Port: who a learner knows, and who has asked to."""

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


@dataclass(frozen=True, slots=True)
class FriendRequestView:
    """Somebody who has asked to add this learner, and is waiting.

    The sender's half is deliberately not modelled: there is no "sent requests"
    list, for the same reason the deck share sheet has no way to withdraw an
    invitation — declining is the recipient's alone and is never reported back,
    so a list of unanswered requests would be a list of people who may simply
    have said no.
    """

    username: str
    name: str
    requested_at: datetime | None


class FriendRepository(ABC):
    @abstractmethod
    async def list_for_user(self, user_id: UUID) -> list[FriendView]:
        """Accepted friends only, most-recently-shared first, never-shared last.

        A request nobody has answered is not a friend, and listing one here is
        how the sender would come to believe something about another person that
        has not happened yet.
        """

    @abstractmethod
    async def list_requests_for(self, user_id: UUID) -> list[FriendRequestView]:
        """Requests waiting on this user to answer, newest first."""

    @abstractmethod
    async def request(self, user_id: UUID, friend_user_id: UUID, *, at: datetime) -> None:
        """Ask to add somebody. Idempotent, and never demotes a friendship.

        Asking again refreshes the request that is out rather than stacking a
        second — and if the two are already friends it changes nothing, so a
        stale client cannot turn an answered question back into an open one.
        """

    @abstractmethod
    async def link(
        self, user_id: UUID, friend_user_id: UUID, *, shared_at: datetime | None = None
    ) -> None:
        """Record an **accepted** link, without asking anyone.

        This is the share path: sharing a deck records the recipient on the
        sender's list, which is what makes a handle something typed once. It
        needs no consent because it reveals nothing the sender did not already
        know, and because the recipient has a deck offer of their own to answer
        — two questions about one act is one question too many.
        """

    @abstractmethod
    async def accept(self, user_id: UUID, requester_id: UUID, *, at: datetime) -> bool:
        """Agree to a request, in both directions. False if there was none.

        A friendship somebody agreed to is mutual, so this writes the reciprocal
        row as well: the person who asked appears on the accepter's list, and the
        accepter on theirs. Returning False rather than raising lets the caller
        distinguish "no such request" from a failure.
        """

    @abstractmethod
    async def unlink(self, user_id: UUID, friend_user_id: UUID) -> None:
        """Remove the link in **both** directions.

        Removing somebody who agreed to be added has to remove you from their
        list too, or one half of a mutual friendship outlives the other and they
        go on seeing somebody who no longer sees them.
        """

    @abstractmethod
    async def decline(self, user_id: UUID, requester_id: UUID) -> None:
        """Delete a request addressed to this user. The sender is not told."""
