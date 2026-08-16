"""Shared fixtures for API integration tests.

The full FastAPI app runs against a per-test database: ``get_session`` is overridden
with a factory bound to a per-test engine, and outbound OTP delivery is captured in an
in-memory outbox so tests can drive the real request→verify flow.

By default the database is in-memory SQLite. Set ``TEST_DATABASE_URL`` (an async DSN,
e.g. ``postgresql+asyncpg://...``) to run the same suite against real Postgres, as CI
does; the schema is created and dropped around every test.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.api.deps import _otp_ip_limiter, get_otp_sender
from app.application.ports.otp_sender import OTPSender
from app.core.config import settings
from app.core.database import Base, get_session
from app.core.security import create_access_token
from app.domain.enums import AuthMethod
from app.infrastructure.db.models.user import UserModel
from app.infrastructure.db.models.word import WordModel
from app.infrastructure.db.models.word_progress import WordProgressModel
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


@pytest.fixture(autouse=True)
def _fresh_otp_ip_limiter() -> None:
    """Empty the per-IP OTP budget between tests — it is process-global state."""
    _otp_ip_limiter.reset()


_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")


@pytest.fixture(autouse=True)
def _fresh_shared_limiters() -> Iterator[None]:
    """Give every test the full rate-limit budget.

    The Redis-backed limiters are process-global and their counters outlive a
    test — without this the suite throttles itself, and which test fails
    depends on the order they ran in.
    """
    from app.api import deps

    deps._hourly_shared_limiter.cache_clear()
    deps._shared_limit_fallback.reset()
    # A developer with Redis running would otherwise share one budget across
    # every run of the suite, so which test fails would depend on how many
    # times it had been run today.
    previous = (
        settings.joins_per_ip_per_hour,
        settings.joins_per_user_per_hour,
        settings.username_checks_per_user_per_hour,
        settings.feedback_reports_per_user_per_hour,
        settings.ai_feedback_per_user_per_hour,
    )
    settings.joins_per_ip_per_hour = 0
    settings.joins_per_user_per_hour = 0
    settings.username_checks_per_user_per_hour = 0
    settings.feedback_reports_per_user_per_hour = 0
    settings.ai_feedback_per_user_per_hour = 0
    yield
    (
        settings.joins_per_ip_per_hour,
        settings.joins_per_user_per_hour,
        settings.username_checks_per_user_per_hour,
        settings.feedback_reports_per_user_per_hour,
        settings.ai_feedback_per_user_per_hour,
    ) = previous
    deps._hourly_shared_limiter.cache_clear()


@pytest.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    if _TEST_DATABASE_URL:
        engine = create_async_engine(_TEST_DATABASE_URL)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        yield engine
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()
        return

    # StaticPool keeps the single in-memory database alive across sessions.
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)

    # SQLite ships with foreign keys off; enable them so ON DELETE CASCADE
    # behaves like Postgres does in production.
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_fks(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

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


async def sleep_on_it(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    days: int = 1,
) -> None:
    """Let a night pass over every card, so unreviewed ones come due.

    A word is first reviewed the day *after* it is added — writing it down is
    the first exposure, and testing it in the same minute measures nothing. A
    test that wants a due queue therefore has to wait a day, and this is how it
    waits: by moving the cards back rather than the clock forward, which keeps
    the tests free of time control.
    """
    shift = timedelta(days=days)
    async with session_factory() as session:
        # Row by row rather than one arithmetic UPDATE: the timestamps go
        # through a custom type that binds datetimes, not intervals.
        for word in (await session.execute(select(WordModel))).scalars().all():
            word.created_at = word.created_at - shift
        for progress in (await session.execute(select(WordProgressModel))).scalars().all():
            progress.due_at = progress.due_at - shift
        await session.commit()


async def spread_due_times_over_today(
    session_factory: async_sessionmaker[AsyncSession],
    hours: Sequence[int],
) -> None:
    """Scatter the progress rows across today at the given hours (UTC).

    The companion to :func:`sleep_on_it`, and the thing it cannot do. Because
    ``sleep_on_it`` shifts rows backwards by whole days it preserves each one's
    time of day exactly, so every card in a test comes due at the same o'clock
    — which is precisely the case where scheduling from the clock instead of
    from the day looks correct.

    Cards due at 00:30, 12:00 and 23:30 all belong to *today*, whatever the
    hour the suite happens to run at. Anything comparing ``due_at`` to ``now``
    will disagree, and disagree differently in the morning than at night.
    """
    async with session_factory() as session:
        rows = (await session.execute(select(WordProgressModel))).scalars().all()
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        for index, progress in enumerate(rows):
            progress.due_at = midnight + timedelta(hours=hours[index % len(hours)])
        await session.commit()
