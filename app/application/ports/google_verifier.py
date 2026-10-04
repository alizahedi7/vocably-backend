"""Port: verification of a Google OAuth/OIDC id_token."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    """Verified identity extracted from a Google id_token."""

    sub: str
    email: str | None
    name: str | None
    #: Whether Google is *authoritative* for ``email`` — not merely whether the
    #: token says ``email_verified``. Google verifies a third-party address once,
    #: when the account is made, and the mailbox can change hands afterwards; it
    #: only speaks for addresses it hosts. An account is found by email at
    #: sign-in on the strength of this flag alone, so an adapter that cannot
    #: tell must leave it False.
    email_verified: bool = False


class GoogleVerifier(ABC):
    @abstractmethod
    async def verify(self, id_token: str) -> GoogleIdentity:
        """Verify a Google id_token and return the identity, or raise on failure."""
