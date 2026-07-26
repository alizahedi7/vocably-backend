"""User ORM model."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, String
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
    age_range: Mapped[str | None] = mapped_column(String(32))
    native_language: Mapped[str] = mapped_column(String(64), default="English", nullable=False)
    app_language: Mapped[str] = mapped_column(String(64), default="English", nullable=False)

    interests: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=list, nullable=False
    )
    daily_goal: Mapped[int] = mapped_column(default=10, nullable=False)

    streak: Mapped[int] = mapped_column(default=0, nullable=False)
    last_studied_on: Mapped[date | None] = mapped_column(Date)
    onboarded: Mapped[bool] = mapped_column(default=False, nullable=False)

    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
