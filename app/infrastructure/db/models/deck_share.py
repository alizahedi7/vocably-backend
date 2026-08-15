"""One learner offering a deck to another.

Distinct from ``deck_invites``, and the difference is load-bearing: an invite
is a link anyone holding it can redeem, while this is addressed to one person
by handle and waits for them to say yes. Accepting makes them a member of the
*same* deck — person-to-person sharing shares the deck; only Explore takes a
copy.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.infrastructure.db.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.infrastructure.db.types import UTCDateTime


class DeckShareModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "deck_shares"
    __table_args__ = (
        # Re-sharing the same deck to the same person updates the existing
        # offer rather than stacking a second one in their list.
        UniqueConstraint("deck_id", "to_user_id", name="uq_deck_shares_deck_recipient"),
        # "What has been sent to me", newest first.
        Index("ix_deck_shares_recipient", "to_user_id", "shared_at"),
    )

    deck_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("decks.id", ondelete="CASCADE"), nullable=False
    )
    #: SET NULL: the offer survives the sender deleting their account, because
    #: what matters to the recipient is the deck, not who sent it.
    from_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    to_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: What accepting makes them. Viewer by default, like every other way in.
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    shared_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    #: Vestigial, and always ``False`` on a row that exists.
    #:
    #: Accepting used to set this and keep the row, so the recipient's list
    #: could show what they had already taken. That turned an inbox into a
    #: permanent record — a card with no action left on it — and outlived the
    #: membership it created, so somebody removed from a deck went on seeing it
    #: under Shared. Accepting now deletes the row, exactly as declining does.
    #:
    #: The column stays because the field is still on the wire: an Android
    #: build months old reads ``accepted`` off ``GET /decks/shared`` and lags
    #: the server by weeks. It reads ``false`` for every row it is now sent,
    #: which is true, and which those builds already render correctly.
    accepted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
