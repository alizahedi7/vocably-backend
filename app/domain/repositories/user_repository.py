"""Port: persistence contract for :class:`~app.domain.entities.user.User`."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date
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
    async def get_by_verified_email(self, email: str) -> User | None:
        """The one account that has *proven* ``email``, already normalised.

        Verified only, on purpose. An address a Google token merely claimed may
        sit on any number of rows; matching those would sign someone in to an
        account on the strength of a string nobody checked.
        """

    @abstractmethod
    async def reload(self, user_id: UUID) -> User | None:
        """Read the row again, past whatever this unit of work already holds.

        For the moment after losing a race, when the copy read at the start of
        the request is the one thing known to be out of date.
        """

    @abstractmethod
    async def link_phone(self, user_id: UUID, phone: str) -> User:
        """Attach a proven phone, replacing any the account had.

        One narrow statement, and the unique index is what decides: two
        accounts verifying one number in the same instant both pass the check
        before it, and the loser gets ``IdentifierInUseError``.
        """

    @abstractmethod
    async def link_email(self, user_id: UUID, email: str) -> User:
        """Attach a proven email and mark it verified, replacing any it had.

        The Google identity is left alone — changing an address must not
        remove a way in. Raises ``IdentifierInUseError`` at the unique index,
        as :meth:`link_phone` does.
        """

    @abstractmethod
    async def unlink_phone(self, user_id: UUID) -> User | None:
        """Remove the phone, or return ``None`` if that would lock the account.

        Guarded in the statement itself — it matches only while a Google
        identity remains — so removing the phone on one device and the email on
        another in the same second cannot both succeed.
        """

    @abstractmethod
    async def unlink_email(self, user_id: UUID) -> User | None:
        """Remove the email **and the Google identity**, or return ``None`` if
        that would lock the account.

        Both, because Google sign-in is how an email signs in: leaving the
        identity behind would let the Google account back in to an account
        whose owner had just removed it. Matches only while a phone remains.
        """

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
    async def bank_day(self, user_id: UUID, today: date) -> bool:
        """Advance the streak for ``today``. True when *this* call did it.

        One statement, never read-modify-write: a session finishing on the
        phone and one finishing in the PWA in the same second both run it, and
        exactly one matches the guard. The boolean is therefore not a
        convenience — it is the only trustworthy answer to "did I cross it?",
        and it is what a client may celebrate on.

        Idempotent by the day, not by the request: calling it a second time on
        a day already banked changes nothing and answers ``False``.
        """

    @abstractmethod
    async def claim_goal_celebration(self, user_id: UUID, today: date) -> bool:
        """Claim the right to congratulate this learner for ``today``.

        Exactly like :meth:`bank_day`, and for the same reason: one guarded
        statement, whose rowcount is the only trustworthy answer. The question
        it answers is different, though — ``bank_day`` asks "did this request
        *win* the day", this asks "is this the client that gets to *say so*".

        They come apart whenever a learner is signed in on more than one
        device. The day is banked once, by whichever device was reviewing; the
        other one later reads ``day_state: banked`` on a plain refresh and,
        with no lock but its own storage, threw the same confetti again. That
        is one celebration per device per day, which is not what "once a day"
        means to the person reading it.

        Idempotent by the day, not by the request: a second call on a day
        already claimed changes nothing and answers ``False``.
        """

    @abstractmethod
    async def settle_streak(self, user_id: UUID, *, days: int, last_day: date | None) -> None:
        """Write back a streak settled at read time.

        Narrow on purpose — it touches two columns and nothing else, so a home
        screen refreshing cannot clobber a profile edit racing it. Callers pass
        the result of :func:`app.domain.services.streak.settle` and only when
        it differs from what they read, which makes this at most one write per
        learner per day.
        """

    @abstractmethod
    async def delete(self, user_id: UUID) -> None:
        """Erase the account.

        Everything keyed on the user cascades: their progress, their review
        history, their memberships, their daily activity. Cards they wrote in
        *other people's* decks survive, uncredited. Callers must delete the
        decks the user owns first — see ``UserService.delete_account``.
        """
