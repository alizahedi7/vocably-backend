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
    """One unanswered request, seen from either end.

    The same two identity fields a friend carries plus when it was sent, and
    which end it is read from is the caller's question rather than the view's:
    :meth:`FriendRepository.list_requests_for` names whoever is asking, and
    :meth:`FriendRepository.list_sent_by` names whoever is being asked.

    **The sender's half used to be deliberately absent**, on the reasoning that
    declining is silent and never reported back, so a list of unanswered
    requests would double as a list of people who may simply have said no. The
    cost of that turned out to be larger than the leak it prevented: somebody
    who asked to add a friend saw a toast and then nothing at all — no record on
    any screen that they had asked, no way to tell an unanswered request from
    one that never left the device, and no way to take one back. Every product
    that does this shows the sender their sent requests, and for that reason.

    What survives is the part that mattered. Nobody is *told* they were
    declined: a declined request leaves this list exactly as an accepted one
    does, and the difference between them is visible only in whether a
    friendship appeared. The sender learns "this is no longer outstanding",
    which is also true of a request they withdrew themselves, and may ask again.
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
    async def list_sent_by(self, user_id: UUID) -> list[FriendRequestView]:
        """Requests **this** user has sent that nobody has answered, newest first.

        The mirror of :meth:`list_requests_for`, over the same rows read from
        the other end. It is what lets the sender see that an ask is out — see
        :class:`FriendRequestView` for why that is worth the little it reveals.
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

    @abstractmethod
    async def cancel(self, user_id: UUID, friend_user_id: UUID) -> None:
        """Withdraw an unanswered request **this** user sent.

        Deliberately narrower than :meth:`unlink`, which removes a friendship in
        both directions whatever its state: taking back a question must not be
        able to end a friendship, and the two arrive at the same row the moment
        the other person accepts. So this touches an unaccepted outgoing row and
        nothing else, and withdrawing one that has just been accepted does
        nothing rather than quietly unfriending somebody who said yes.
        """
