"""Port: persistence contract for OTP challenges."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.entities.otp_challenge import OtpChallenge


class OTPChallengeRepository(ABC):
    @abstractmethod
    async def get_active_by_phone(self, phone: str) -> OtpChallenge | None:
        """Return the most recent non-consumed challenge for a phone, if any."""

    @abstractmethod
    async def add(self, challenge: OtpChallenge) -> OtpChallenge: ...

    @abstractmethod
    async def update(self, challenge: OtpChallenge) -> OtpChallenge: ...

    @abstractmethod
    async def invalidate_for_phone(self, phone: str) -> None:
        """Mark all outstanding challenges for a phone as consumed."""
