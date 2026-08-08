"""Someone a learner has shared with before.

A recency list, not a social graph: the need is "don't make me type a handle
twice". One-directional and needing no consent, because it reveals nothing the
sharer did not already know — they typed the handle. Turning it into a mutual
friendship would need an accept step, and that is a different feature.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.infrastructure.db.models.mixins import TimestampMixin
from app.infrastructure.db.types import UTCDateTime


class FriendLinkModel(TimestampMixin, Base):
    __tablename__ = "friend_links"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    friend_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    #: NULL for someone added by hand who has never been shared with. Orders
    #: the list, so the people you actually send things to stay at the top.
    last_shared_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
