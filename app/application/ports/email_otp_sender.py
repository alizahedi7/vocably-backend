"""Port: outbound delivery of one-time passcodes by email."""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmailOTPSender(ABC):
    @abstractmethod
    async def send(self, email: str, code: str) -> None:
        """Deliver ``code`` to ``email``. Raise on unrecoverable delivery failure."""
