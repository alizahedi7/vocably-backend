"""Deck ORM model."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.infrastructure.db.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class DeckModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "decks"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    hue: Mapped[int] = mapped_column(default=262, nullable=False)
