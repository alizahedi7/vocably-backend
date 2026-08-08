"""The append-only ledger behind a learner's experience points.

Same hybrid the review history uses: immutable events here, a counter on
``users.xp`` that every request reads. The counter is what the profile screen
needs; the ledger is what makes "why am I level 7" answerable and lets the
award table change without rewriting anyone's history.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, Index, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.infrastructure.db.models.mixins import UUIDPrimaryKeyMixin
from app.infrastructure.db.types import UTCDateTime


class XpEventModel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "xp_events"
    __table_args__ = (
        Index("ix_xp_events_user_occurred", "user_id", "occurred_at"),
        # "The daily goal pays once a day" enforced by the database, not by an
        # application check that two sessions finishing together would both
        # pass. Partial, because every other action may repeat freely.
        Index(
            "uq_xp_events_daily_goal",
            "user_id",
            "action",
            "day",
            unique=True,
            postgresql_where=text("action = 'daily_goal'"),
            sqlite_where=text("action = 'daily_goal'"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: An ``XpAction`` value, mirroring the client's enum.
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    points: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    #: The learner's *local* day, and the reason a partial unique index can
    #: enforce "the daily goal pays once" in the database rather than in an
    #: application check that races with itself.
    day: Mapped[date] = mapped_column(Date, nullable=False)
    ref_type: Mapped[str | None] = mapped_column(String(16))
    ref_id: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
