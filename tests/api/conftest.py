"""Shared fixtures for API integration tests.

The full FastAPI app runs against an in-memory SQLite database: ``get_session`` is
overridden with a factory bound to a per-test engine, and outbound OTP delivery is
captured in an in-memory outbox so tests can drive the real request→verify flow.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.api.deps import get_otp_sender
from app.application.ports.otp_sender import OTPSender
from app.core.config import settings
from app.core.database import Base, get_session
from app.core.security import create_access_token
from app.domain.enums import AuthMethod
from app.infrastructure.db.models.user import UserModel
from app.main import app

UserFactory = Callable[..., Awaitable[UserModel]]


class RecordingOTPSender(OTPSender):
    """Captures sent codes instead of delivering them."""

    def __init__(self) -> None:
        self.outbox: list[tuple[str, str]] = []

    async def send(self, phone: str, code: str) -> None:
        self.outbox.append((phone, code))

    def last_code_for(self, phone: str) -> str:
        codes = [code for to, code in self.outbox if to == phone]
        assert codes, f"no OTP was sent to {phone}"
        return codes[-1]


@pytest.fixture(autouse=True)
def _no_otp_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the resend cooldown so flow tests can request codes back to back.

    The cooldown itself is exercised explicitly in ``test_auth_otp``.
    """
    monkeypatch.setattr(settings, "otp_resend_cooldown_seconds", 0)


@pytest.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    # StaticPool keeps the single in-memory database alive across sessions.
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
def otp_sender() -> RecordingOTPSender:
    return RecordingOTPSender()


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    otp_sender: RecordingOTPSender,
) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
        # Mirrors app.core.database.get_session: commit on success, rollback on error.
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_otp_sender] = lambda: otp_sender
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


@pytest.fixture
def make_user(session_factory: async_sessionmaker[AsyncSession]) -> UserFactory:
    async def _make_user(**overrides: Any) -> UserModel:
        fields: dict[str, Any] = {
            "auth_method": AuthMethod.PHONE.value,
            "phone": "+989121234567",
            "name": "Ali",
            "onboarded": True,
        }
        fields.update(overrides)
        user = UserModel(**fields)
        async with session_factory() as session:
            session.add(user)
            await session.commit()
            await session.refresh(user)
        return user

    return _make_user


@pytest.fixture
async def user(make_user: UserFactory) -> UserModel:
    return await make_user()


@pytest.fixture
def auth_headers(user: UserModel) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def bearer(user_id: Any) -> dict[str, str]:
    """Auth headers for an arbitrary user id (e.g. a second user in ownership tests)."""
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}
