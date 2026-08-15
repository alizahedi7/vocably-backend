"""One direction of "these two people know each other".

It began as a recency list rather than a social graph — the need was "don't make
me type a handle twice" — and it was one-directional and consent-free on the
reasoning that it revealed nothing the sharer did not already know, because they
had typed the handle themselves.

The people search ended that. A handle is *found* now, so being added is no
longer something the other person had already disclosed to you, and they were
never told it had happened. ``accepted`` is the answer: false is a request
waiting on its recipient, true is a friendship. Accepting writes the reciprocal
row, so an agreed friendship is held by both people — the asymmetry survives only
where nobody was asked, which is the share path below.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.infrastructure.db.models.mixins import TimestampMixin
from app.infrastructure.db.types import UTCDateTime


class FriendLinkModel(TimestampMixin, Base):
    __tablename__ = "friend_links"
    __table_args__ = (
        # "Who has asked to add me?" — read on every foregrounded poll, and by
        # the *second* key column, which the primary key cannot serve.
        Index("ix_friend_links_incoming", "friend_user_id", "accepted"),
    )

    #: Whose list this row is on. For a pending request, the sender.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    friend_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    #: NULL for someone added by hand who has never been shared with. Orders
    #: the list, so the people you actually send things to stay at the top.
    last_shared_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    #: False while the request is waiting on ``friend_user_id`` to answer.
    #:
    #: Sharing a deck writes an accepted row directly, and that is deliberate
    #: rather than an oversight: it records that the sender sent something, on
    #: the sender's own list, and the recipient has a *deck* offer to answer.
    #: Making them approve a friendship as well would be two questions about one
    #: act. Nothing is revealed either — the sharer already knew the handle,
    #: which is the original reasoning, still sound where it applies.
    accepted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
