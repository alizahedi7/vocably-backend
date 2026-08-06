"""Deck invite-link ORM model. One row per deck; re-opening reuses it."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.infrastructure.db.models.mixins import TimestampMixin
from app.infrastructure.db.types import UTCDateTime


class DeckInviteModel(TimestampMixin, Base):
    __tablename__ = "deck_invites"

    #: The deck *is* the key: one link per deck, so re-opening a closed link
    #: hands back the same code. A code that changed on every open would
    #: invalidate one already handed to a class.
    deck_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("decks.id", ondelete="CASCADE"), primary_key=True
    )
    #: Unique-indexed because it is looked up directly on join, and because two
    #: decks sharing a code would be an access-control failure, not a collision.
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
