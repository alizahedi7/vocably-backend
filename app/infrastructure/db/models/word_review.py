"""Append-only review-event log ORM model.

This is the highest-volume table in the schema by a wide margin — one row per
press of a grading button, forever — so it diverges from the conventions the
other models follow, deliberately and in three ways:

1. **A ``bigint`` identity key, not the usual UUID.** Rows arrive in an endless
   append; random UUIDv4 keys would scatter every insert across the whole
   B-tree, splitting pages and dirtying cold buffers. A monotonic integer always
   lands on the rightmost leaf page.
2. **No ``TimestampMixin``.** Rows are never updated, so ``updated_at`` would be
   dead weight, and ``reviewed_at`` already *is* the creation time.
3. **Partitioned by month in Postgres** (see the migration). Retention becomes an
   instant ``DROP TABLE`` per month instead of a bulk ``DELETE`` that leaves the
   heap bloated for weeks.

The Postgres table's real primary key is the composite ``(id, reviewed_at)`` —
a partitioned table must include its partition key in every unique constraint.
That is declared in the migration rather than here so this model stays valid on
SQLite, where a composite key would break identity generation. Alembic does not
autogenerate primary-key changes, so the difference produces no migration noise.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.infrastructure.db.types import UTCDateTime


class WordReviewModel(Base):
    __tablename__ = "word_reviews"
    __table_args__ = (
        # "This card's history" — the per-word AI features and the learning curve.
        Index("ix_word_reviews_word", "word_id", "reviewed_at"),
        # "This user's reviews over a period" — daily rollups, streaks, analytics.
        Index("ix_word_reviews_user_reviewed", "user_id", "reviewed_at"),
        # Guard the persisted enum encodings at the storage layer: a row that
        # cannot be decoded back into a ReviewGrade/LeitnerBox is worse than a
        # rejected insert, because it is only discovered at read time.
        CheckConstraint("grade BETWEEN 0 AND 3", name="ck_word_reviews_grade"),
        CheckConstraint("box_before BETWEEN 1 AND 5", name="ck_word_reviews_box_before"),
        CheckConstraint("box_after BETWEEN 1 AND 5", name="ck_word_reviews_box_after"),
    )

    # BigInteger has no auto-increment on SQLite (only a plain INTEGER primary
    # key aliases the rowid), hence the variant; Identity is ignored there.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        Identity(always=False),
        primary_key=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    word_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("words.id", ondelete="CASCADE"), nullable=False
    )
    # No foreign key by design: this records which deck the card was in *at
    # review time*. Cards move between decks and decks get deleted; neither
    # should rewrite or erase history that already happened.
    deck_id: Mapped[uuid.UUID] = mapped_column(nullable=False)

    reviewed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    grade: Mapped[int] = mapped_column(SmallInteger(), nullable=False)
    box_before: Mapped[int] = mapped_column(SmallInteger(), nullable=False)
    box_after: Mapped[int] = mapped_column(SmallInteger(), nullable=False)

    #: NULL on a card's first ever review.
    elapsed_seconds: Mapped[int | None] = mapped_column(Integer())
    #: Signed — negative when the learner reviewed ahead of the due date.
    overdue_seconds: Mapped[int | None] = mapped_column(Integer())
    #: Client-reported and untrusted; clamped in the domain layer.
    latency_ms: Mapped[int | None] = mapped_column(Integer())
    #: Groups one sitting. Not indexed: sessions are read via the user index.
    session_id: Mapped[uuid.UUID | None] = mapped_column()
