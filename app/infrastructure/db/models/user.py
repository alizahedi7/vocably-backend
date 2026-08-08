"""User ORM model."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.infrastructure.db.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.infrastructure.db.types import UTCDateTime


class UserModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    auth_method: Mapped[str] = mapped_column(String(16), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)

    name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    #: The handle other people type: sharing, friends and the roster all address
    #: a person by it. Stored already-lowercased with a unique index rather than
    #: relying on the application to normalise, so two casings cannot both exist.
    #: Nullable because accounts predating handles have none until backfilled.
    username: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)
    age_range: Mapped[str | None] = mapped_column(String(32))
    native_language: Mapped[str] = mapped_column(String(64), default="English", nullable=False)
    app_language: Mapped[str] = mapped_column(String(64), default="English", nullable=False)
    #: The language being learned.
    target_language: Mapped[str | None] = mapped_column(String(64))
    #: A key from the client's kProficiencyLevels. Deliberately not an enum:
    #: the list is a product surface that changes faster than a migration.
    proficiency: Mapped[str | None] = mapped_column(String(32))
    #: A key, not a time — it pre-fills the Android reminder.
    study_time: Mapped[str | None] = mapped_column(String(32))
    #: IANA name, e.g. "Asia/Tehran". Every day and week boundary is computed
    #: from it; see app/domain/services/calendar.py. NULL means UTC.
    timezone: Mapped[str | None] = mapped_column(String(64))

    interests: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=list, nullable=False
    )
    daily_goal: Mapped[int] = mapped_column(default=10, nullable=False)

    streak: Mapped[int] = mapped_column(default=0, nullable=False)
    last_studied_on: Mapped[date | None] = mapped_column(Date)
    onboarded: Mapped[bool] = mapped_column(default=False, nullable=False)

    #: Experience points. A counter kept in step with the ``xp_events`` ledger,
    #: and the whole contract with the client: it derives level and progress
    #: from this single integer.
    xp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
