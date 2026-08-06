"""Who belongs to a deck, and in what role.

This table — not ``decks.user_id`` — is the authorization source for every
deck- and word-scoped route. ``decks.user_id`` stays as creator attribution.

It lands with the ``words``/``word_progress`` split rather than with the sharing
endpoints because the split *defines* a learner's word set as "words in every
deck I am a member of": the due queue and every aggregate join through here from
the moment progress stops living on the card.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.infrastructure.db.models.mixins import TimestampMixin
from app.infrastructure.db.types import UTCDateTime


class DeckMemberModel(TimestampMixin, Base):
    __tablename__ = "deck_members"
    __table_args__ = (
        # "Decks shared with me" — the deck list's driving access path.
        Index("ix_deck_members_user", "user_id"),
    )

    deck_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("decks.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    #: A ``DeckRole`` value. Stored as text rather than a database enum so
    #: adding a role is a deploy, not a migration; unknown values parse to
    #: ``viewer``, the least privileged.
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Attribution for the roster, and nothing else. No FK action needed: the
    #: inviter leaving does not change anyone's membership.
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    joined_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
