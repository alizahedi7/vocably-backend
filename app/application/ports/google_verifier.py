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


class GoogleVerifier(ABC):
    @abstractmethod
    async def verify(self, id_token: str) -> GoogleIdentity:
        """Verify a Google id_token and return the identity, or raise on failure."""
