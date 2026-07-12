"""OTP challenge entity — a pending phone-verification attempt."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

MAX_OTP_ATTEMPTS = 5


@dataclass(slots=True)
class OtpChallenge:
    id: UUID = field(default_factory=uuid4)
    phone: str = ""
    code_hash: str = ""
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    attempts: int = 0
    consumed: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def is_exhausted(self) -> bool:
        return self.attempts >= MAX_OTP_ATTEMPTS

    def is_usable(self, now: datetime) -> bool:
        return not self.consumed and not self.is_expired(now) and not self.is_exhausted()
