"""Linking a phone and an email to one account, and taking either off again.

An account is one row whatever it was created with. This service is how the
second identifier gets onto it — proven by a code first, always — and how one
comes off without leaving the account with no way in.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from app.application.dto import LinkCodeIssued
from app.application.ports.email_otp_sender import EmailOTPSender
from app.application.ports.otp_sender import OTPSender
from app.application.ports.rate_limiter import RateLimiter
from app.core.config import settings
from app.core.exceptions import (
    ConflictError,
    IdentifierInUseError,
    InvalidLinkCodeError,
    LastSignInMethodError,
    RateLimitedError,
    ValidationError,
)
from app.core.security import generate_otp, hash_otp, verify_otp
from app.domain.entities.link_challenge import LinkChallenge
from app.domain.entities.user import User
from app.domain.enums import IdentifierType
from app.domain.repositories.link_challenge_repository import LinkChallengeRepository
from app.domain.repositories.user_repository import UserRepository
from app.domain.services import identifiers

# 4xx messages here are user-visible copy: the app renders ``detail`` verbatim.
_INVALID_TARGET = {
    IdentifierType.EMAIL: "Enter a valid email address.",
    IdentifierType.PHONE: "Enter your phone number with its country code, like +989121234567.",
}
_ALREADY_YOURS = {
    IdentifierType.EMAIL: "That email is already linked to your account.",
    IdentifierType.PHONE: "That phone number is already linked to your account.",
}
# There is no merge, so the copy says what does work: the identifier has to
# come off the account that holds it before it can go on this one.
_IN_USE = {
    IdentifierType.EMAIL: (
        "That email is already used by another Vocably account. To use it here, "
        "sign in to that account and remove it there, or delete that account."
    ),
    IdentifierType.PHONE: (
        "That phone number is already used by another Vocably account. To use it "
        "here, sign in to that account and remove it there, or delete that account."
    ),
}
_LAST_WAY_IN = {
    IdentifierType.EMAIL: (
        "Your email is the only way to sign in to this account. "
        "Add a phone number before removing it."
    ),
    IdentifierType.PHONE: (
        "Your phone number is the only way to sign in to this account. "
        "Link an email and sign in with Google once before removing it."
    ),
}


class AccountLinkService:
    def __init__(
        self,
        users: UserRepository,
        challenges: LinkChallengeRepository,
        sms_sender: OTPSender,
        email_sender: EmailOTPSender,
        cooldown: RateLimiter,
        hourly: RateLimiter,
    ) -> None:
        self._users = users
        self._challenges = challenges
        self._sms_sender = sms_sender
        self._email_sender = email_sender
        self._cooldown = cooldown
        self._hourly = hourly

    async def request_code(self, user: User, kind: IdentifierType, target: str) -> LinkCodeIssued:
        """Send a code to ``target`` so ``user`` can prove it is theirs."""
        target = _normalized(kind, target)
        if user.holds(kind, target):
            raise ConflictError(_ALREADY_YOURS[kind])

        # Throttle *before* asking whether the identifier is taken, and that
        # order is the control. "Already in use" tells the caller that a phone
        # number or address has a Vocably account, so every question has to
        # cost a slot — checked the other way round, the refusals would be free
        # and this would be an account-enumeration oracle with no budget.
        await self._throttle(user, kind, target)
        await self._ensure_free(user, kind, target)

        code = generate_otp()
        now = datetime.now(UTC)
        await self._challenges.put(
            LinkChallenge(
                user_id=user.id,
                kind=kind,
                target=target,
                code_hash=hash_otp(code),
                attempts_left=settings.link_otp_max_attempts,
                expires_at=now + timedelta(seconds=settings.link_otp_ttl_seconds),
                created_at=now,
            )
        )
        # Last, so a delivery failure raises out of the request and the stored
        # challenge rolls back with it: no code exists that nobody was sent.
        if kind is IdentifierType.EMAIL:
            await self._email_sender.send(target, code)
        else:
            await self._sms_sender.send(target, code)
        return LinkCodeIssued(
            kind=kind,
            target=target,
            expires_in_seconds=settings.link_otp_ttl_seconds,
            resend_after_seconds=max(settings.link_otp_cooldown_seconds, 0),
        )

    async def verify_code(self, user: User, kind: IdentifierType, target: str, code: str) -> User:
        """Check the code and attach ``target`` to ``user``.

        On a wrong code this spends an attempt and raises; the caller must
        commit that spend or the three-attempt cap never engages.
        """
        target = _normalized(kind, target)
        if user.holds(kind, target):
            return user

        challenge = await self._challenges.get(user.id, kind)
        # The target is part of the proof: a code sent to one address says
        # nothing about another, so a mismatch is the same as no challenge.
        if (
            challenge is None
            or challenge.target != target
            or not challenge.is_usable(datetime.now(UTC))
        ):
            raise InvalidLinkCodeError()

        if not verify_otp(code.strip(), challenge.code_hash):
            left = await self._challenges.spend_attempt(user.id, kind)
            if left > 0:
                tries = "1 try" if left == 1 else f"{left} tries"
                raise InvalidLinkCodeError(f"That code isn't right — {tries} left.")
            raise InvalidLinkCodeError()

        if not await self._challenges.consume(user.id, kind, challenge.code_hash):
            # Lost a race for the same code, which is a second tap on "verify".
            # If the first tap linked it, this one succeeded too.
            current = await self._users.reload(user.id)
            if current is not None and current.holds(kind, target):
                return current
            raise InvalidLinkCodeError()

        # The unique index decides, not the check made when the code was sent:
        # another account can prove the same identifier in the five minutes
        # between, and only one row can hold it.
        try:
            if kind is IdentifierType.PHONE:
                return await self._users.link_phone(user.id, target)
            return await self._users.link_email(user.id, target)
        except IdentifierInUseError as exc:
            raise IdentifierInUseError(_IN_USE[kind]) from exc

    async def unlink(self, user: User, kind: IdentifierType) -> User:
        """Take an identifier off the account, unless it is the last way in.

        A way in is a phone (a texted code) or a Google identity (Google
        sign-in). A verified email with no Google identity behind it is *not*
        one — nothing signs in with it until Google has — so it cannot be what
        is left standing when the phone goes.

        Idempotent: removing what is not there answers the account unchanged.
        """
        if kind is IdentifierType.PHONE:
            updated = await self._users.unlink_phone(user.id)
        else:
            updated = await self._users.unlink_email(user.id)
        if updated is None:
            raise LastSignInMethodError(_LAST_WAY_IN[kind])
        return updated

    async def _throttle(self, user: User, kind: IdentifierType, target: str) -> None:
        scope = f"{user.id}:{kind.value}"
        if settings.link_otp_cooldown_seconds > 0 and not await self._cooldown.allow(
            f"link-otp-cooldown:{scope}", 1
        ):
            raise RateLimitedError("Please wait a minute before requesting another code.")
        per_hour = settings.link_otp_requests_per_hour
        if per_hour > 0 and not await self._hourly.allow(f"link-otp:{scope}", per_hour):
            raise RateLimitedError("Too many codes requested. Please try again in an hour.")
        per_target = settings.link_otp_requests_per_target_per_hour
        # Hashed: a limiter key is not somewhere a phone number should sit.
        digest = hashlib.sha256(target.encode()).hexdigest()[:32]
        if per_target > 0 and not await self._hourly.allow(f"link-otp-target:{digest}", per_target):
            raise RateLimitedError("Too many codes requested. Please try again in an hour.")

    async def _ensure_free(self, user: User, kind: IdentifierType, target: str) -> None:
        if kind is IdentifierType.PHONE:
            holder = await self._users.get_by_phone(target)
        else:
            holder = await self._users.get_by_verified_email(target)
        if holder is not None and holder.id != user.id:
            raise IdentifierInUseError(_IN_USE[kind])


def _normalized(kind: IdentifierType, raw: str) -> str:
    target = identifiers.normalize(kind, raw)
    if target is None:
        raise ValidationError(_INVALID_TARGET[kind])
    return target
