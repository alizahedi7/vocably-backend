"""Dependency-injection wiring — the composition root.

This module is where concrete adapters get plugged into the ports the application layer
declares. Everything above (domain/application) stays ignorant of these choices.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.admin_repository import AdminRepository
from app.application.ports.ai_service import AIService
from app.application.ports.dictionary_service import DictionaryService
from app.application.ports.google_verifier import GoogleVerifier
from app.application.ports.lookup_cache import LookupCacheRepository
from app.application.ports.otp_sender import OTPSender
from app.application.services.admin_service import AdminService
from app.application.services.ai_studio_service import MAX_LOOKUP_SUGGESTIONS, AIStudioService
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
from app.domain.repositories.review_event_repository import ReviewEventRepository
from app.domain.repositories.user_repository import UserRepository
from app.domain.repositories.word_repository import WordRepository
from app.infrastructure.ai.caching_ai_service import CachingAIService
from app.infrastructure.ai.grounded_ai_service import GroundedAIService, SenseTranslator
from app.infrastructure.ai.prompts import PROMPT_VERSION
from app.infrastructure.ai.stub_ai_service import StubAIService
from app.infrastructure.ai.translate_prompts import TRANSLATE_PROMPT_VERSION
from app.infrastructure.auth.console_otp_sender import ConsoleOTPSender
from app.infrastructure.auth.google_id_token_verifier import GoogleIdTokenVerifier
from app.infrastructure.auth.kavenegar_otp_sender import KavenegarOTPSender
from app.infrastructure.auth.sms_ir_otp_sender import SmsIrOTPSender
from app.infrastructure.auth.stub_google_verifier import StubGoogleVerifier
from app.infrastructure.db.repositories.admin_repository import SqlAlchemyAdminRepository
from app.infrastructure.db.repositories.deck_repository import SqlAlchemyDeckRepository
from app.infrastructure.db.repositories.lookup_cache_repository import (
    SqlAlchemyLookupCacheRepository,
)
from app.infrastructure.db.repositories.otp_repository import (
    SqlAlchemyOTPChallengeRepository,
)
from app.infrastructure.db.repositories.review_event_repository import (
    SqlAlchemyReviewEventRepository,
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


def get_lookup_cache_repository(session: SessionDep) -> LookupCacheRepository:
    return SqlAlchemyLookupCacheRepository(session)


def get_review_event_repository(session: SessionDep) -> ReviewEventRepository:
    return SqlAlchemyReviewEventRepository(session)


UserRepoDep = Annotated[UserRepository, Depends(get_user_repository)]
DeckRepoDep = Annotated[DeckRepository, Depends(get_deck_repository)]
WordRepoDep = Annotated[WordRepository, Depends(get_word_repository)]
OTPRepoDep = Annotated[OTPChallengeRepository, Depends(get_otp_repository)]
AdminRepoDep = Annotated[AdminRepository, Depends(get_admin_repository)]
LookupCacheRepoDep = Annotated[LookupCacheRepository, Depends(get_lookup_cache_repository)]
ReviewEventRepoDep = Annotated[ReviewEventRepository, Depends(get_review_event_repository)]


# ── Outbound adapters (selected by config) ───────────────────
def get_ai_provider() -> AIService:
    """The lookup pipeline below the cache: provider, grounded when enabled."""
    provider = _raw_ai_provider()
    if not settings.dictionary_enabled:
        return provider
    return GroundedAIService(
        provider,
        _dictionary_service(),
        # The provider doubles as the translator: it satisfies SenseTranslator
        # structurally, and reusing it keeps one HTTP client and one set of
        # response guardrails for both paths.
        translator=cast("SenseTranslator", provider),
        max_cards=MAX_LOOKUP_SUGGESTIONS,
        rewrite_definitions=settings.dictionary_rewrite_definitions,
    )


def _raw_ai_provider() -> AIService:
    """The provider adapter itself, before grounding or caching."""
    if settings.ai_provider == "anthropic":
        if not settings.anthropic_api_key:
            raise RuntimeError("AI_PROVIDER=anthropic requires ANTHROPIC_API_KEY.")
        return _anthropic_ai_service()
    if settings.ai_provider == "avalai":
        if not settings.avalai_api_key or not settings.avalai_model:
            raise RuntimeError("AI_PROVIDER=avalai requires AVALAI_API_KEY and AVALAI_MODEL.")
        return _avalai_ai_service()
    return StubAIService()


@lru_cache
def _dictionary_service() -> DictionaryService:
    """The dictionary adapter and its Redis cache, built once per process.

    Memoized for the same reason the provider adapters are: both the HTTP
    connection pool and the Redis pool should outlive a single request. Redis is
    created lazily and never awaited here, so an unreachable Redis costs nothing
    at startup — every cache call is best-effort and a dead one degrades to
    calling the dictionary directly.
    """
    import httpx
    from redis.asyncio import Redis

    from app.infrastructure.dictionary.free_dictionary_service import (
        DictionaryCache,
        FreeDictionaryService,
    )

    return FreeDictionaryService(
        httpx.AsyncClient(),
        DictionaryCache(
            Redis.from_url(settings.dictionary_redis_url, decode_responses=True),
            hit_ttl_seconds=settings.dictionary_cache_ttl_seconds,
            miss_ttl_seconds=settings.dictionary_cache_miss_ttl_seconds,
        ),
        timeout_seconds=settings.dictionary_timeout_seconds,
    )


def _effective_prompt_version() -> int:
    """The cache-key version for the whole lookup pipeline, not one prompt.

    Grounded and generated cards read differently, so they must never share a
    key — otherwise flipping ``DICTIONARY_ENABLED`` would serve a mix of the two
    and make a rollback invisible. Encoding both versions in one integer keeps
    ``LookupCacheKey`` unchanged and lets either prompt be bumped independently.
    """
    if not settings.dictionary_enabled:
        return PROMPT_VERSION
    # The two grounded modes write visibly different card fronts — one shows the
    # dictionary's wording, the other a rewrite — so they get distinct keys too.
    mode = 2 if settings.dictionary_rewrite_definitions else 1
    return (PROMPT_VERSION * 1000 + TRANSLATE_PROMPT_VERSION) * 10 + mode


def _configured_model() -> str:
    """The model name to record on cached entries, for provenance only."""
    if settings.ai_provider == "anthropic":
        return settings.anthropic_model
    if settings.ai_provider == "avalai":
        return settings.avalai_model
    return ""


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


@lru_cache
def _avalai_ai_service() -> AIService:
    # Cached so one HTTP client (and its connection pool) is shared across
    # requests instead of being rebuilt per lookup.
    from app.infrastructure.ai.avalai_ai_service import AvalAIService

    return AvalAIService(
        api_key=settings.avalai_api_key,
        model=settings.avalai_model,
        base_url=settings.avalai_base_url,
        timeout_seconds=settings.avalai_timeout_seconds,
        max_tokens=settings.avalai_max_tokens,
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


AIProviderDep = Annotated[AIService, Depends(get_ai_provider)]


def get_ai_service(provider: AIProviderDep, cache: LookupCacheRepoDep) -> AIService:
    """The AI service the use cases get: the provider, cached when enabled.

    Composed per request because the cache repository is bound to the
    request-scoped session, while the provider adapter itself stays
    process-wide (see ``_anthropic_ai_service``) so its connection pool is
    reused. The wrapper is a plain object — building one per request costs
    nothing and opens no connections.
    """
    if not settings.ai_cache_enabled:
        return provider
    return CachingAIService(
        provider,
        cache,
        unsupported_ttl_seconds=settings.ai_cache_unsupported_ttl_seconds,
        provider=settings.ai_provider,
        model=_configured_model(),
        prompt_version=_effective_prompt_version(),
    )


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


def get_study_service(
    words: WordRepoDep, users: UserRepoDep, reviews: ReviewEventRepoDep
) -> StudyService:
    return StudyService(words, users, reviews)


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
