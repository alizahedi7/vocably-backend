"""ORM models for the two feedback tables.

Both keep their ``user_id`` nullable with ``ON DELETE SET NULL`` rather than
``CASCADE``, which is the one non-obvious decision here and applies for two
different reasons:

* A **report** is the record of a bug. The account that reported it going away
  does not make the bug go away, and cascading would mean a deleted account
  silently withdraws a defect report nobody has fixed yet. The sentence stays;
  the link to the person does not.
* A **rating** is not personal data at all — its entire value is
  ``(lookup_id, sense_index, rating)`` — so deletion anonymises it instead of
  destroying a signal about card quality that was never about the rater.

In both cases the user's own account deletion still removes every link back to
them, which is what the deletion is for.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.infrastructure.db.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class FeedbackReportModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One thing a learner wrote to us from Settings → Send feedback."""

    __tablename__ = "feedback_reports"
    __table_args__ = (
        # The triage list is "newest first, optionally filtered by kind", and
        # that is the only way this table is ever read.
        Index("ix_feedback_reports_kind_created_at", "kind", "created_at"),
        Index("ix_feedback_reports_created_at", "created_at"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    #: ``FeedbackKind``. Stored as text rather than a database enum so adding a
    #: fourth kind is a deploy, not a migration with a lock on it.
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    # ── the device, as it described itself ───────────────────
    # Four short columns rather than one JSON blob: they are a fixed, known set
    # that triage filters and groups by ("only on web", "only since 1.5.0"),
    # and a blob would make each of those a scan.
    app_version: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    platform: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    os_version: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="")


class AIFeedbackModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One learner's thumb on one sense of one AI lookup."""

    __tablename__ = "ai_feedback"
    __table_args__ = (
        # The identity of a verdict. A learner changing their mind updates this
        # row; it is also what makes the endpoint idempotent under the silent
        # retries the client can make without telling anyone.
        UniqueConstraint(
            "user_id", "lookup_id", "sense_index", name="uq_ai_feedback_user_lookup_sense"
        ),
        # The aggregate: "score every rated sense". Leads with the grouping key
        # so the roll-up is an index scan rather than a sort of the table.
        Index("ix_ai_feedback_lookup_sense", "lookup_id", "sense_index"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    #: ``LookupCacheKey.digest()`` for the resolved term — the same value as
    #: ``ai_lookup_entries.entry_hash``, and deliberately *not* a foreign key to
    #: it: entries are swept when a prompt version retires, and a verdict on a
    #: retired prompt is exactly the thing worth keeping. Joined when the entry
    #: is there, standing alone when it is not.
    lookup_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Which card back in the deck, 0-based.
    sense_index: Mapped[int] = mapped_column(Integer, nullable=False)

    #: ``AIRating`` — only ever ``up`` or ``down``. Withdrawing deletes the row.
    rating: Mapped[str] = mapped_column(String(8), nullable=False)
    #: ``AIFeedbackReason``, or NULL. NULL is the ordinary case: the chips are
    #: offered after the rating has already landed and most people ignore them.
    reason: Mapped[str | None] = mapped_column(String(24))

    # ── provenance, denormalised on purpose ──────────────────
    # Copied from the cache entry at rating time so the row still answers "which
    # word, written by what" once that entry has been swept.
    term: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    native_language: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")

    # No separate ``rated_at``: an upsert is the only thing that ever writes
    # this row, so ``TimestampMixin.updated_at`` already *is* "when the verdict
    # last moved". A second column saying the same thing is one that can drift.
