"""Port: persistence contract for link challenges."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.entities.link_challenge import LinkChallenge
from app.domain.enums import IdentifierType


class LinkChallengeRepository(ABC):
    @abstractmethod
    async def put(self, challenge: LinkChallenge) -> None:
        """Store ``challenge``, replacing the one outstanding for this user and kind.

        One row per ``(user, kind)``: asking again retires the previous code
        rather than leaving two that both work.
        """

    @abstractmethod
    async def get(self, user_id: UUID, kind: IdentifierType) -> LinkChallenge | None: ...

    @abstractmethod
    async def spend_attempt(self, user_id: UUID, kind: IdentifierType) -> int:
        """Count one wrong guess, and return how many are left.

        Decremented in SQL, never written back from a value read earlier, so
        guesses sent side by side cannot share one attempt between them.
        """

    @abstractmethod
    async def consume(self, user_id: UUID, kind: IdentifierType, code_hash: str) -> bool:
        """Delete the challenge. True when *this* call was the one that did.

        The single-use guarantee: two requests carrying the same correct code
        both run this, and exactly one matches a row.
        """
