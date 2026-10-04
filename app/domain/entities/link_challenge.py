"""Link challenge entity — a code sent to prove a phone or email before it is
attached to an account that already exists."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from app.domain.enums import IdentifierType


@dataclass(slots=True)
class LinkChallenge:
    """One outstanding code, for one account and one kind of identifier.

    Distinct from :class:`~app.domain.entities.otp_challenge.OtpChallenge`,
    which signs in whoever holds a phone. This one belongs to a signed-in user
    and is bound to the ``target`` it was sent to — so a code that proved one
    address can never be spent attaching another.
    """

    user_id: UUID
    kind: IdentifierType
    target: str
    code_hash: str
    attempts_left: int
    expires_at: datetime
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def is_usable(self, now: datetime) -> bool:
        return self.attempts_left > 0 and now < self.expires_at
