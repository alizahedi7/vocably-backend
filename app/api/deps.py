"""Dependency-injection wiring — the composition root.

This module is where concrete adapters get plugged into the ports the application layer
declares. Everything above (domain/application) stays ignorant of these choices.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.ai_service import AIService
from app.application.ports.google_verifier import GoogleVerifier
from app.application.ports.otp_sender import OTPSender
from app.application.services.ai_studio_service import AIStudioService
from app.application.services.auth_service import AuthService
from app.application.services.deck_service import DeckService
from app.application.services.study_service import StudyService
from app.application.services.user_service import UserService
from app.application.services.word_service import WordService
from app.core.database import get_session
from app.core.exceptions import AuthenticationError
from app.core.security import TokenType, decode_token
from app.domain.entities.user import User
from app.domain.repositories.deck_repository import DeckRepository
from app.domain.repositories.otp_repository import OTPChallengeRepository
from app.domain.repositories.user_repository import UserRepository
from app.domain.repositories.word_repository import WordRepository
from app.infrastructure.ai.stub_ai_service import StubAIService
from app.infrastructure.auth.console_otp_sender import ConsoleOTPSender
from app.infrastructure.auth.stub_google_verifier import StubGoogleVerifier
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


UserRepoDep = Annotated[UserRepository, Depends(get_user_repository)]
DeckRepoDep = Annotated[DeckRepository, Depends(get_deck_repository)]
WordRepoDep = Annotated[WordRepository, Depends(get_word_repository)]
OTPRepoDep = Annotated[OTPChallengeRepository, Depends(get_otp_repository)]


# ── Outbound adapters (selected by config) ───────────────────
def get_ai_service() -> AIService:
    # Only the stub ships today. Add anthropic/openai branches here later.
    return StubAIService()


def get_otp_sender() -> OTPSender:
    return ConsoleOTPSender()


def get_google_verifier() -> GoogleVerifier:
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


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
UserServiceDep = Annotated[UserService, Depends(get_user_service)]
DeckServiceDep = Annotated[DeckService, Depends(get_deck_service)]
WordServiceDep = Annotated[WordService, Depends(get_word_service)]
StudyServiceDep = Annotated[StudyService, Depends(get_study_service)]
AIStudioServiceDep = Annotated[AIStudioService, Depends(get_ai_studio_service)]


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
