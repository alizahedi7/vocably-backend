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
from app.application.ports.google_verifier import GoogleVerifier
from app.application.ports.lookup_cache import LookupCacheRepository
from app.application.ports.otp_sender import OTPSender
from app.application.services.admin_service import AdminService
from app.application.services.ai_studio_service import AIStudioService
from app.application.services.auth_service import AuthService
from app.application.services.content_admin_service import ContentAdminService
from app.application.services.deck_build_service import DeckBuildService
from app.application.services.deck_discovery_service import DeckDiscoveryService
from app.application.services.deck_service import DeckService
from app.application.services.deck_sharing_service import DeckSharingService
from app.application.services.deck_unit_service import DeckUnitService
from app.application.services.friend_service import FriendService
from app.application.services.lexicon_service import LexiconService
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
from app.core.rate_limit import RedisFixedWindowRateLimiter, SlidingWindowRateLimiter
from app.core.security import TokenType, decode_token
from app.domain.entities.user import User
from app.domain.repositories.deck_activity_repository import DeckActivityRepository
from app.domain.repositories.deck_build_repository import DeckBuildRepository
from app.domain.repositories.deck_discovery_repository import DeckDiscoveryRepository
from app.domain.repositories.deck_invite_repository import DeckInviteRepository
from app.domain.repositories.deck_member_repository import DeckMemberRepository
from app.domain.repositories.deck_repository import DeckRepository
from app.domain.repositories.deck_unit_repository import DeckUnitRepository
from app.domain.repositories.friend_repository import FriendRepository
from app.domain.repositories.lexicon_repository import LexiconRepository
from app.domain.repositories.otp_repository import OTPChallengeRepository
from app.domain.repositories.review_event_repository import ReviewEventRepository
from app.domain.repositories.user_repository import UserRepository
from app.domain.repositories.word_progress_repository import WordProgressRepository
from app.domain.repositories.word_repository import WordRepository
from app.domain.repositories.xp_repository import XpRepository
from app.infrastructure.ai.factory import (
    configured_model,
    effective_prompt_version,
    grounded_ai_provider,
    lookup_chain,
    raw_ai_provider,
)
from app.infrastructure.ai.lexicon_ai_service import SenseEnricher
from app.infrastructure.auth.console_otp_sender import ConsoleOTPSender
from app.infrastructure.auth.google_id_token_verifier import GoogleIdTokenVerifier
from app.infrastructure.auth.kavenegar_otp_sender import KavenegarOTPSender
from app.infrastructure.auth.sms_ir_otp_sender import SmsIrOTPSender
from app.infrastructure.auth.stub_google_verifier import StubGoogleVerifier
from app.infrastructure.db.repositories.admin_repository import SqlAlchemyAdminRepository
from app.infrastructure.db.repositories.deck_activity_repository import (
    SqlAlchemyDeckActivityRepository,
)
from app.infrastructure.db.repositories.deck_build_repository import (
    SqlAlchemyDeckBuildRepository,
)
from app.infrastructure.db.repositories.deck_discovery_repository import (
    SqlAlchemyDeckDiscoveryRepository,
)
from app.infrastructure.db.repositories.deck_invite_repository import (
    SqlAlchemyDeckInviteRepository,
)
from app.infrastructure.db.repositories.deck_member_repository import (
    SqlAlchemyDeckMemberRepository,
)
from app.infrastructure.db.repositories.deck_repository import SqlAlchemyDeckRepository
from app.infrastructure.db.repositories.deck_unit_repository import (
    SqlAlchemyDeckUnitRepository,
)
from app.infrastructure.db.repositories.friend_repository import (
    SqlAlchemyFriendRepository,
)
from app.infrastructure.db.repositories.lexicon_repository import (
    SqlAlchemyLexiconRepository,
)
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
from app.infrastructure.db.repositories.word_progress_repository import (
    SqlAlchemyWordProgressRepository,
)
from app.infrastructure.db.repositories.word_repository import SqlAlchemyWordRepository
from app.infrastructure.db.repositories.xp_repository import SqlAlchemyXpRepository
from app.infrastructure.dictionary.factory import dictionary_service

SessionDep = Annotated[AsyncSession, Depends(get_session)]


# ── Repositories ─────────────────────────────────────────────
def get_user_repository(session: SessionDep) -> UserRepository:
    return SqlAlchemyUserRepository(session)


def get_deck_repository(session: SessionDep) -> DeckRepository:
    return SqlAlchemyDeckRepository(session)


def get_word_repository(session: SessionDep) -> WordRepository:
    return SqlAlchemyWordRepository(session)


def get_word_progress_repository(session: SessionDep) -> WordProgressRepository:
    return SqlAlchemyWordProgressRepository(session)


def get_deck_member_repository(session: SessionDep) -> DeckMemberRepository:
    return SqlAlchemyDeckMemberRepository(session)


def get_deck_unit_repository(session: SessionDep) -> DeckUnitRepository:
    return SqlAlchemyDeckUnitRepository(session)


def get_deck_invite_repository(session: SessionDep) -> DeckInviteRepository:
    return SqlAlchemyDeckInviteRepository(session)


def get_deck_activity_repository(session: SessionDep) -> DeckActivityRepository:
    return SqlAlchemyDeckActivityRepository(session)


def get_deck_discovery_repository(session: SessionDep) -> DeckDiscoveryRepository:
    return SqlAlchemyDeckDiscoveryRepository(session)


def get_friend_repository(session: SessionDep) -> FriendRepository:
    return SqlAlchemyFriendRepository(session)


def get_xp_repository(session: SessionDep) -> XpRepository:
    return SqlAlchemyXpRepository(session)


def get_otp_repository(session: SessionDep) -> OTPChallengeRepository:
    return SqlAlchemyOTPChallengeRepository(session)


def get_admin_repository(session: SessionDep) -> AdminRepository:
    return SqlAlchemyAdminRepository(session)


def get_lookup_cache_repository(session: SessionDep) -> LookupCacheRepository:
    return SqlAlchemyLookupCacheRepository(session)


def get_review_event_repository(session: SessionDep) -> ReviewEventRepository:
    return SqlAlchemyReviewEventRepository(session)


def get_lexicon_repository(session: SessionDep) -> LexiconRepository:
    return SqlAlchemyLexiconRepository(session)


def get_deck_build_repository(session: SessionDep) -> DeckBuildRepository:
    return SqlAlchemyDeckBuildRepository(session)


UserRepoDep = Annotated[UserRepository, Depends(get_user_repository)]
DeckRepoDep = Annotated[DeckRepository, Depends(get_deck_repository)]
WordRepoDep = Annotated[WordRepository, Depends(get_word_repository)]
WordProgressRepoDep = Annotated[WordProgressRepository, Depends(get_word_progress_repository)]
DeckMemberRepoDep = Annotated[DeckMemberRepository, Depends(get_deck_member_repository)]
DeckUnitRepoDep = Annotated[DeckUnitRepository, Depends(get_deck_unit_repository)]
DeckInviteRepoDep = Annotated[DeckInviteRepository, Depends(get_deck_invite_repository)]
DeckActivityRepoDep = Annotated[DeckActivityRepository, Depends(get_deck_activity_repository)]
DeckDiscoveryRepoDep = Annotated[DeckDiscoveryRepository, Depends(get_deck_discovery_repository)]
FriendRepoDep = Annotated[FriendRepository, Depends(get_friend_repository)]
XpRepoDep = Annotated[XpRepository, Depends(get_xp_repository)]
OTPRepoDep = Annotated[OTPChallengeRepository, Depends(get_otp_repository)]
AdminRepoDep = Annotated[AdminRepository, Depends(get_admin_repository)]
LookupCacheRepoDep = Annotated[LookupCacheRepository, Depends(get_lookup_cache_repository)]
ReviewEventRepoDep = Annotated[ReviewEventRepository, Depends(get_review_event_repository)]
LexiconRepoDep = Annotated[LexiconRepository, Depends(get_lexicon_repository)]
DeckBuildRepoDep = Annotated[DeckBuildRepository, Depends(get_deck_build_repository)]


# ── Outbound adapters (selected by config) ───────────────────
# Construction lives in ``app.infrastructure.ai.factory`` because the deck-build
# worker needs the identical chain without importing FastAPI. These are thin
# aliases so the dependency graph still reads from this one composition root.
def get_ai_provider() -> AIService:
    """The lookup pipeline below the lexicon: provider, grounded when enabled."""
    return grounded_ai_provider()


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


def get_lexicon_service(lexicon: LexiconRepoDep) -> LexiconService:
    """Reads and writes to the durable lexicon, bound to this request's session."""
    return LexiconService(
        lexicon,
        content_version=effective_prompt_version(),
        provider=settings.ai_provider,
        model=configured_model(),
    )


LexiconServiceDep = Annotated[LexiconService, Depends(get_lexicon_service)]


def get_ai_service(
    provider: AIProviderDep,
    session: SessionDep,
    lexicon: LexiconServiceDep,
) -> AIService:
    """The AI service the use cases get — see ``factory.lookup_chain`` for the order.

    Composed per request because both the cache repository and the lexicon
    repository are bound to the request-scoped session, while the provider
    adapter stays process-wide so its connection pool is reused. The wrappers are
    plain objects: building them per request costs nothing and opens nothing.
    """
    return lookup_chain(session, lexicon, provider=provider)


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


def get_user_service(
    users: UserRepoDep, decks: DeckRepoDep, members: DeckMemberRepoDep
) -> UserService:
    return UserService(users, decks, members)


def get_deck_service(
    decks: DeckRepoDep,
    progress: WordProgressRepoDep,
    members: DeckMemberRepoDep,
    users: UserRepoDep,
) -> DeckService:
    return DeckService(decks, progress, members, users)


def get_word_service(
    words: WordRepoDep,
    progress: WordProgressRepoDep,
    members: DeckMemberRepoDep,
    units: DeckUnitRepoDep,
    xp: XpRepoDep,
    users: UserRepoDep,
) -> WordService:
    return WordService(words, progress, members, units, xp, users)


def get_deck_unit_service(units: DeckUnitRepoDep, members: DeckMemberRepoDep) -> DeckUnitService:
    return DeckUnitService(units, members)


def get_study_service(
    progress: WordProgressRepoDep,
    users: UserRepoDep,
    reviews: ReviewEventRepoDep,
    activity: DeckActivityRepoDep,
    xp: XpRepoDep,
) -> StudyService:
    return StudyService(progress, users, reviews, activity, xp)


def get_deck_discovery_service(
    discovery: DeckDiscoveryRepoDep,
    members: DeckMemberRepoDep,
    invites: DeckInviteRepoDep,
    users: UserRepoDep,
    friends: FriendRepoDep,
) -> DeckDiscoveryService:
    return DeckDiscoveryService(discovery, members, invites, users, friends)


def get_deck_build_service(
    builds: DeckBuildRepoDep,
    decks: DeckRepoDep,
    members: DeckMemberRepoDep,
    units: DeckUnitRepoDep,
    words: WordRepoDep,
    discovery: DeckDiscoveryRepoDep,
    lexicon: LexiconServiceDep,
    ai: AIServiceDep,
) -> DeckBuildService:
    """The build pipeline, wired to the same AI chain a learner's lookup uses.

    Deliberately not given a private path to the provider: reuse is structural
    only if the builder goes through the cache and the lexicon like everyone
    else. The enricher is the raw provider adapter, which satisfies
    :class:`SenseEnricher` structurally — the wrappers above it have nothing to
    add to a call that is already asking for what the lexicon does not hold.
    """
    return DeckBuildService(
        builds,
        decks,
        members,
        units,
        words,
        discovery,
        lexicon,
        ai,
        content_version=effective_prompt_version(),
        enricher=cast("SenseEnricher", raw_ai_provider()),
        dictionary=dictionary_service() if settings.dictionary_available else None,
        claim_timeout_seconds=settings.deck_build_claim_timeout_seconds,
    )


def get_friend_service(friends: FriendRepoDep, users: UserRepoDep) -> FriendService:
    return FriendService(friends, users)


def get_deck_sharing_service(
    members: DeckMemberRepoDep,
    invites: DeckInviteRepoDep,
    users: UserRepoDep,
    activity: DeckActivityRepoDep,
) -> DeckSharingService:
    return DeckSharingService(members, invites, users, activity)


def get_ai_studio_service(
    ai: AIServiceDep,
    progress: WordProgressRepoDep,
    users: UserRepoDep,
) -> AIStudioService:
    return AIStudioService(ai, progress, users)


def get_admin_service(admin_repo: AdminRepoDep) -> AdminService:
    return AdminService(admin_repo)


def get_content_admin_service(
    builds: DeckBuildRepoDep,
    lexicon: LexiconRepoDep,
    words: WordRepoDep,
    build_service: DeckBuildServiceDep,
) -> ContentAdminService:
    return ContentAdminService(
        builds,
        lexicon,
        words,
        build_service,
        current_content_version=effective_prompt_version(),
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
UserServiceDep = Annotated[UserService, Depends(get_user_service)]
DeckServiceDep = Annotated[DeckService, Depends(get_deck_service)]
WordServiceDep = Annotated[WordService, Depends(get_word_service)]
DeckUnitServiceDep = Annotated[DeckUnitService, Depends(get_deck_unit_service)]
DeckSharingServiceDep = Annotated[DeckSharingService, Depends(get_deck_sharing_service)]
DeckDiscoveryServiceDep = Annotated[DeckDiscoveryService, Depends(get_deck_discovery_service)]
FriendServiceDep = Annotated[FriendService, Depends(get_friend_service)]
DeckBuildServiceDep = Annotated[DeckBuildService, Depends(get_deck_build_service)]
StudyServiceDep = Annotated[StudyService, Depends(get_study_service)]
AIStudioServiceDep = Annotated[AIStudioService, Depends(get_ai_studio_service)]
AdminServiceDep = Annotated[AdminService, Depends(get_admin_service)]
ContentAdminServiceDep = Annotated[ContentAdminService, Depends(get_content_admin_service)]


# ── Abuse protection ─────────────────────────────────────────
_otp_ip_limiter = SlidingWindowRateLimiter(window_seconds=3600.0)
#: Backs the Redis limiters when Redis is unreachable, so the limit degrades to
#: per-worker rather than disappearing.
_shared_limit_fallback = SlidingWindowRateLimiter(window_seconds=3600.0)


@lru_cache
def _hourly_shared_limiter() -> RedisFixedWindowRateLimiter:
    """One Redis connection pool for every hourly security limit, per process.

    Built lazily and never awaited here, so an unreachable Redis costs nothing
    at startup — the first call falls back and logs.
    """
    from redis.asyncio import Redis

    return RedisFixedWindowRateLimiter(
        Redis.from_url(settings.rate_limit_redis_url, decode_responses=True),
        window_seconds=3600,
        fallback=_shared_limit_fallback,
    )


async def enforce_join_limit(request: Request, current_user: CurrentUser) -> None:
    """Cap invite-code redemptions per user *and* per IP.

    The one endpoint where guessing wins access to someone else's data. Codes
    carry ~65 bits so guessing is already hopeless, but a limit is what keeps
    that true if the code format is ever shortened, and it caps the damage of a
    leaked-then-revoked link being hammered.
    """
    limiter = _hourly_shared_limiter()
    per_user = settings.joins_per_user_per_hour
    if per_user > 0 and not await limiter.allow(f"join-user:{current_user.id}", per_user):
        raise RateLimitedError("Too many join attempts. Please try again later.")
    per_ip = settings.joins_per_ip_per_hour
    if per_ip > 0:
        client_ip = request.client.host if request.client else "unknown"
        if not await limiter.allow(f"join-ip:{client_ip}", per_ip):
            raise RateLimitedError("Too many join attempts. Please try again later.")


async def enforce_share_limit(current_user: CurrentUser) -> None:
    """Cap sharing and friend-adds per user.

    Both take a handle and tell the caller whether it exists, so they are a
    slower version of the availability oracle and need the same treatment.
    """
    limit = settings.shares_per_user_per_hour
    if limit <= 0:
        return
    if not await _hourly_shared_limiter().allow(f"share:{current_user.id}", limit):
        raise RateLimitedError("Too many shares just now. Please try again shortly.")


async def enforce_username_check_limit(current_user: CurrentUser) -> None:
    """Cap handle-availability checks per user.

    The endpoint answers "does this person exist" for any string, so without a
    limit it is a handle-enumeration oracle. Keyed per user, not per IP: it
    requires a token, and a shared IP is a classroom.
    """
    limit = settings.username_checks_per_user_per_hour
    if limit <= 0:
        return
    if not await _hourly_shared_limiter().allow(f"username-check:{current_user.id}", limit):
        raise RateLimitedError("Too many handle checks. Please try again shortly.")


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
