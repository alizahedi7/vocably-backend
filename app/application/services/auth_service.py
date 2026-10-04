"""Authentication use cases: phone/OTP and Google sign-in, plus token refresh."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.application.dto import AuthResult, TokenPair
from app.application.ports.google_verifier import GoogleVerifier
from app.application.ports.otp_sender import OTPSender
from app.core.config import settings
from app.core.exceptions import InvalidOTPError, InvalidTokenError, RateLimitedError
from app.core.logging import get_logger
from app.core.security import (
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_otp,
    hash_otp,
    verify_otp,
)
from app.domain.entities.otp_challenge import OtpChallenge
from app.domain.entities.user import User
from app.domain.enums import AuthMethod
from app.domain.repositories.otp_repository import OTPChallengeRepository
from app.domain.repositories.user_repository import UserRepository
from app.domain.services import identifiers

logger = get_logger(__name__)


class AuthService:
    def __init__(
        self,
        users: UserRepository,
        otp_challenges: OTPChallengeRepository,
        otp_sender: OTPSender,
        google_verifier: GoogleVerifier,
    ) -> None:
        self._users = users
        self._otp_challenges = otp_challenges
        self._otp_sender = otp_sender
        self._google = google_verifier

    # ── Phone / OTP ───────────────────────────────────────────
    async def request_otp(self, phone: str) -> None:
        """Create and deliver a fresh OTP challenge for ``phone``."""
        phone = phone.strip()
        code = generate_otp()
        now = datetime.now(UTC)

        latest = await self._otp_challenges.get_active_by_phone(phone)
        cooldown = timedelta(seconds=settings.otp_resend_cooldown_seconds)
        if latest is not None and now - latest.created_at < cooldown:
            raise RateLimitedError("Please wait before requesting a new code.")

        await self._otp_challenges.invalidate_for_phone(phone)
        await self._otp_challenges.add(
            OtpChallenge(
                phone=phone,
                code_hash=hash_otp(code),
                expires_at=now + timedelta(seconds=settings.otp_ttl_seconds),
            )
        )
        await self._otp_sender.send(phone, code)

    async def verify_otp(self, phone: str, code: str) -> AuthResult:
        """Verify an OTP; sign the user in, creating the account if new."""
        phone = phone.strip()
        now = datetime.now(UTC)
        challenge = await self._otp_challenges.get_active_by_phone(phone)

        if challenge is None or not challenge.is_usable(now):
            raise InvalidOTPError()

        if not verify_otp(code, challenge.code_hash):
            await self._otp_challenges.record_failed_attempt(challenge.id)
            raise InvalidOTPError()

        challenge.consumed = True
        await self._otp_challenges.update(challenge)

        user = await self._users.get_by_phone(phone)
        if user is None:
            user = await self._users.add(
                User(auth_method=AuthMethod.PHONE, phone=phone, last_login_at=now)
            )
            return self._issue(user, is_new=True)
        return await self._sign_in_existing(user, now)

    # ── Google ────────────────────────────────────────────────
    async def sign_in_with_google(self, id_token: str) -> AuthResult:
        """Sign in with Google, landing on the account that holds this identity.

        Found by ``sub`` first — the one thing about a Google account that
        never changes. Failing that, by an email its owner has *verified* here,
        and only when Google is authoritative for that address: this is what
        makes an email linked to a phone account open the same account. An
        address Google merely repeats is never used to find anyone.
        """
        now = datetime.now(UTC)
        identity = await self._google.verify(id_token)
        email = identifiers.normalize_email(identity.email or "")
        vouched = email if identity.email_verified else None

        user = await self._users.get_by_google_sub(identity.sub)
        owner = await self._users.get_by_verified_email(vouched) if vouched else None
        if user is None and owner is not None:
            # Rebinds even over an older Google identity: the address is the
            # credential, and Google says this is the account that holds it now.
            owner.google_sub = identity.sub
            user = owner
        if user is None:
            user = await self._users.add(
                User(
                    auth_method=AuthMethod.GOOGLE,
                    google_sub=identity.sub,
                    email=email,
                    is_email_verified=vouched is not None,
                    name=identity.name or "",
                    last_login_at=now,
                )
            )
            return self._issue(user, is_new=True)
        if vouched and owner is None and not user.is_email_verified:
            # An account from before verification was tracked, or one whose
            # address Google could not vouch for until now. Nobody else has
            # proven it, so it becomes this account's.
            user.email = vouched
            user.is_email_verified = True
        return await self._sign_in_existing(user, now)

    # ── Tokens ────────────────────────────────────────────────
    async def refresh(self, refresh_token: str) -> TokenPair:
        payload = decode_token(refresh_token, expected_type=TokenType.REFRESH)
        user_id = UUID(str(payload["sub"]))
        user = await self._users.get(user_id)
        if user is None:
            raise InvalidTokenError("User no longer exists.")
        return self._tokens_for(user.id)

    async def _sign_in_existing(self, user: User, now: datetime) -> AuthResult:
        """Record the login timestamp for a returning user, then issue tokens."""
        user.last_login_at = now
        user = await self._users.update(user)
        return self._issue(user, is_new=False)

    def _issue(self, user: User, is_new: bool) -> AuthResult:
        return AuthResult(user=user, tokens=self._tokens_for(user.id), is_new_user=is_new)

    @staticmethod
    def _tokens_for(user_id: UUID) -> TokenPair:
        return TokenPair(
            access_token=create_access_token(user_id),
            refresh_token=create_refresh_token(user_id),
        )
