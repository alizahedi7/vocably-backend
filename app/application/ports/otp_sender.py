"""Port: outbound delivery of one-time passcodes (SMS, etc.)."""

from __future__ import annotations

from abc import ABC, abstractmethod


class OTPSender(ABC):
    @abstractmethod
    async def send(self, phone: str, code: str) -> None:
        """Deliver ``code`` to ``phone``. Raise on unrecoverable delivery failure."""
