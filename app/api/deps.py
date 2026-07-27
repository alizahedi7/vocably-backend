"""Dependency-injection wiring — the composition root.

This module is where concrete adapters get plugged into the ports the application layer
declares. Everything above (domain/application) stays ignorant of these choices.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.admin_repository import AdminRepository
from app.application.ports.ai_service import AIService
from app.application.ports.google_verifier import GoogleVerifier
from app.application.ports.otp_sender import OTPSender
from app.application.services.admin_service import AdminService
from app.application.services.ai_studio_service import AIStudioService
from app.application.services.auth_service import AuthService
from app.application.services.deck_service import DeckService
from app.application.services.study_service import StudyService
from app.application.services.user_service import UserService
from app.application.services.word_service import WordService
from app.core.config import settings
from app.core.database import get_session
from app.core.exceptions import (
    AuthenticationError,
    PermissionDeniedError,
    RateLimitedError,
)
from app.core.rate_limit import SlidingWindowRateLimiter
from app.core.security import TokenType, decode_token
from app.domain.entities.user import User
from app.domain.repositories.deck_repository import DeckRepository
from app.domain.repositories.otp_repository import OTPChallengeRepository
from app.domain.repositories.user_repository import UserRepository
from app.domain.repositories.word_repository import WordRepository
from app.infrastructure.ai.stub_ai_service import StubAIService
from app.infrastructure.auth.console_otp_sender import ConsoleOTPSender
from app.infrastructure.auth.google_id_token_verifier import GoogleIdTokenVerifier
from app.infrastructure.auth.kavenegar_otp_sender import KavenegarOTPSender
from app.infrastructure.auth.sms_ir_otp_sender import SmsIrOTPSender
from app.infrastructure.auth.stub_google_verifier import StubGoogleVerifier
from app.infrastructure.db.repositories.admin_repository import SqlAlchemyAdminRepository
from app.infrastructure.db.repositories.deck_repository import SqlAlchemyDeckRepository
from app.infrastructure.db.repositories.otp_repository import (
    SqlAlchemyOTPChallengeRepository,
)
from app.infrastructure.db.repositories.user_repository import SqlAlchemyUserRepository
from app.infrastructure.db.repositories.word_repository import SqlAlchemyWordRepository

SessionDep = Annotated[AsyncSession, Depends(get_session)]


# ── Repositories ─────────────────────────────────────────────
def get_user_repository(session: SessionDep) -> UserRepository:
    return SqlAlchemyUserRepository(session)


def get_deck_repository(session: SessionDep) -> DeckRepository:
    return SqlAlchemyDeckRepository(session)


def get_word_repository(session: SessionDep) -> WordRepository:
    return SqlAlchemyWordRepository(session)


def get_otp_repository(session: SessionDep) -> OTPChallengeRepository:
    return SqlAlchemyOTPChallengeRepository(session)


def get_admin_repository(session: SessionDep) -> AdminRepository:
    return SqlAlchemyAdminRepository(session)


UserRepoDep = Annotated[UserRepository, Depends(get_user_repository)]
DeckRepoDep = Annotated[DeckRepository, Depends(get_deck_repository)]
WordRepoDep = Annotated[WordRepository, Depends(get_word_repository)]
OTPRepoDep = Annotated[OTPChallengeRepository, Depends(get_otp_repository)]
AdminRepoDep = Annotated[AdminRepository, Depends(get_admin_repository)]


# ── Outbound adapters (selected by config) ───────────────────
def get_ai_service() -> AIService:
    if settings.ai_provider == "anthropic":
        if not settings.anthropic_api_key:
            raise RuntimeError("AI_PROVIDER=anthropic requires ANTHROPIC_API_KEY.")
        return _anthropic_ai_service()
    return StubAIService()


@lru_cache
def _anthropic_ai_service() -> AIService:
    # Cached so one HTTP client (and its connection pool) is shared across
    # requests instead of being rebuilt per lookup.
    from app.infrastructure.ai.anthropic_ai_service import AnthropicAIService

    return AnthropicAIService(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        base_url=settings.anthropic_base_url,
        timeout_seconds=settings.anthropic_timeout_seconds,
        max_tokens=settings.anthropic_max_tokens,
        extra_headers=settings.anthropic_header_map,
    )


def get_otp_sender() -> OTPSender:
    if settings.otp_sender == "kavenegar":
        if not settings.kavenegar_api_key or not settings.kavenegar_otp_template:
            raise RuntimeError(
                "OTP_SENDER=kavenegar requires KAVENEGAR_API_KEY and KAVENEGAR_OTP_TEMPLATE."
            )
        return KavenegarOTPSender(
            api_key=settings.kavenegar_api_key,
            template=settings.kavenegar_otp_template,
        )
    if settings.otp_sender == "sms_ir":
        if not settings.sms_ir_api_key or not settings.sms_ir_template_id:
            raise RuntimeError("OTP_SENDER=sms_ir requires SMS_IR_API_KEY and SMS_IR_TEMPLATE_ID.")
        return SmsIrOTPSender(
            api_key=settings.sms_ir_api_key,
            template_id=settings.sms_ir_template_id,
        )
    return ConsoleOTPSender()


@lru_cache
def _google_id_token_verifier() -> GoogleIdTokenVerifier:
    # Memoized so the JWKS key cache survives across requests.
    return GoogleIdTokenVerifier(client_id=settings.google_client_id)


def get_google_verifier() -> GoogleVerifier:
    if settings.google_verifier == "google":
        if not settings.google_client_id:
            raise RuntimeError("GOOGLE_VERIFIER=google requires GOOGLE_CLIENT_ID.")
        return _google_id_token_verifier()
    return StubGoogleVerifier()


AIServiceDep = Annotated[AIService, Depends(get_ai_service)]
OTPSenderDep = Annotated[OTPSender, Depends(get_otp_sender)]
GoogleVerifierDep = Annotated[GoogleVerifier, Depends(get_google_verifier)]


# ── Use-case services ────────────────────────────────────────
def get_auth_service(
    users: UserRepoDep,
    otp_repo: OTPRepoDep,
    otp_sender: OTPSenderDep,
    google: GoogleVerifierDep,
) -> AuthService:
    return AuthService(users, otp_repo, otp_sender, google)


def get_user_service(users: UserRepoDep) -> UserService:
    return UserService(users)


def get_deck_service(decks: DeckRepoDep, words: WordRepoDep) -> DeckService:
    return DeckService(decks, words)


def get_word_service(words: WordRepoDep, decks: DeckRepoDep) -> WordService:
    return WordService(words, decks)


def get_study_service(words: WordRepoDep, users: UserRepoDep) -> StudyService:
    return StudyService(words, users)


def get_ai_studio_service(
    ai: AIServiceDep,
    words: WordRepoDep,
    users: UserRepoDep,
) -> AIStudioService:
    return AIStudioService(ai, words, users)


def get_admin_service(admin_repo: AdminRepoDep) -> AdminService:
    return AdminService(admin_repo)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
UserServiceDep = Annotated[UserService, Depends(get_user_service)]
DeckServiceDep = Annotated[DeckService, Depends(get_deck_service)]
WordServiceDep = Annotated[WordService, Depends(get_word_service)]
StudyServiceDep = Annotated[StudyService, Depends(get_study_service)]
AIStudioServiceDep = Annotated[AIStudioService, Depends(get_ai_studio_service)]
AdminServiceDep = Annotated[AdminService, Depends(get_admin_service)]


# ── Abuse protection ─────────────────────────────────────────
_otp_ip_limiter = SlidingWindowRateLimiter(window_seconds=3600.0)


async def enforce_otp_request_ip_limit(request: Request) -> None:
    """Cap OTP requests per client IP so one caller can't drain the SMS budget.

    Uses the socket peer address; behind a reverse proxy, run uvicorn with
    ``--proxy-headers`` (and ``--forwarded-allow-ips``) so it reflects the
    real client rather than the proxy.
    """
    limit = settings.otp_requests_per_ip_per_hour
    if limit <= 0:
        return
    client_ip = request.client.host if request.client else "unknown"
    if not _otp_ip_limiter.allow(client_ip, limit):
        raise RateLimitedError("Too many verification requests. Please try again later.")


# ── Authentication ───────────────────────────────────────────
_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    users: UserRepoDep,
) -> User:
    if credentials is None:
        raise AuthenticationError("Missing bearer token.")
    payload = decode_token(credentials.credentials, expected_type=TokenType.ACCESS)
    try:
        user_id = UUID(str(payload["sub"]))
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Malformed token subject.") from exc
    user = await users.get(user_id)
    if user is None:
        raise AuthenticationError("User no longer exists.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


# ── Authorization ────────────────────────────────────────────
async def require_admin(current_user: CurrentUser) -> User:
    """Gate a route behind the admin role.

    Layers on top of ``get_current_user`` (so the caller is authenticated first)
    and rejects non-admins with 403 rather than 401 — the token is valid, the
    user simply lacks access.
    """
    if not current_user.is_admin:
        raise PermissionDeniedError("Admin access required.")
    return current_user


CurrentAdmin = Annotated[User, Depends(require_admin)]
