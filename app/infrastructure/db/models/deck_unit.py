"""Deck unit (lesson/chapter) ORM model.

A deck with no units renders exactly as it did before the feature existed, and
a card may belong to no unit — there is deliberately no "uncategorised" unit.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.infrastructure.db.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class DeckUnitModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "deck_units"
    __table_args__ = (
        # Indexed, not unique: a UNIQUE (deck_id, position) would need a
        # deferred constraint or a two-pass update to reorder, and
        # server-assigned gaps are simpler and enough.
        Index("ix_deck_units_deck_position", "deck_id", "position"),
    )

    deck_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("decks.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
