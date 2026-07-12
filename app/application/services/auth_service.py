"""Authentication use cases: phone/OTP and Google sign-in, plus token refresh."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.application.dto import AuthResult, TokenPair
from app.application.ports.google_verifier import GoogleVerifier
from app.application.ports.otp_sender import OTPSender
from app.core.config import settings
from app.core.exceptions import InvalidOTPError, InvalidTokenError
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
            challenge.attempts += 1
            await self._otp_challenges.update(challenge)
            raise InvalidOTPError()

        challenge.consumed = True
        await self._otp_challenges.update(challenge)

        user = await self._users.get_by_phone(phone)
        is_new = user is None
        if user is None:
            user = await self._users.add(User(auth_method=AuthMethod.PHONE, phone=phone))
        return self._issue(user, is_new)

    # ── Google ────────────────────────────────────────────────
    async def sign_in_with_google(self, id_token: str) -> AuthResult:
        identity = await self._google.verify(id_token)
        user = await self._users.get_by_google_sub(identity.sub)
        is_new = user is None
        if user is None:
            user = await self._users.add(
                User(
                    auth_method=AuthMethod.GOOGLE,
                    google_sub=identity.sub,
                    email=identity.email,
                    name=identity.name or "",
                )
            )
        return self._issue(user, is_new)

    # ── Tokens ────────────────────────────────────────────────
    async def refresh(self, refresh_token: str) -> TokenPair:
        payload = decode_token(refresh_token, expected_type=TokenType.REFRESH)
        user_id = UUID(str(payload["sub"]))
        user = await self._users.get(user_id)
        if user is None:
            raise InvalidTokenError("User no longer exists.")
        return self._tokens_for(user.id)

    def _issue(self, user: User, is_new: bool) -> AuthResult:
        return AuthResult(user=user, tokens=self._tokens_for(user.id), is_new_user=is_new)

    @staticmethod
    def _tokens_for(user_id: UUID) -> TokenPair:
        return TokenPair(
            access_token=create_access_token(user_id),
            refresh_token=create_refresh_token(user_id),
        )
