"""Deck ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.infrastructure.db.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.infrastructure.db.types import UTCDateTime


class DeckModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "decks"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    hue: Mapped[int] = mapped_column(default=262, nullable=False)
    #: Slug of a logo the client ships as an asset — set from a deck template,
    #: empty for a deck a learner built. See ``Deck.icon``.
    icon: Mapped[str] = mapped_column(String(40), default="", nullable=False)

    # ── Explore ──────────────────────────────────────────────
    #: Whether the deck is listed for anyone to browse and copy. Writable by
    #: admins only for now: there is no report path and no moderation queue,
    #: and an open publish button without one is an unreviewed-content problem
    #: rather than a feature. See CLAUDE.md.
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    #: A key from the client's kDeckCategories. Free-form for the same reason
    #: proficiency is: the list is a product surface.
    category: Mapped[str] = mapped_column(String(32), default="general", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: Persian description, shown to learners reading the app in Persian.
    description_fa: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: Published by Vocably itself rather than by a learner.
    is_official: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: How many people have taken a copy — the only quality signal Explore
    #: shows, and a better one than a rating nobody fills in.
    save_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: The published deck this one was copied from, or ``None`` for a deck its
    #: owner built. Provenance, and only that: the copy is independent in every
    #: other respect. It exists so Explore can say "Saved" on a deck the learner
    #: already took — without it the tick would have to be remembered on the
    #: device, and would be wrong after a reinstall or on a second phone.
    #: ``SET NULL`` on delete, because unpublishing or removing the original
    #: must not touch the copy.
    copied_from_deck_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("decks.id", ondelete="SET NULL"), index=True, nullable=True
    )
