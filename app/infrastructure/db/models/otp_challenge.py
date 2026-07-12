"""OTP challenge ORM model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.infrastructure.db.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class OtpChallengeModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "otp_challenges"

    phone: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
