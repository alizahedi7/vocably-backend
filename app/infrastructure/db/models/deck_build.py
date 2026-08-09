"""ORM models for deck builds — the resumable plan behind a pre-built deck.

``deck_build_items`` is the coordination primitive for the whole pipeline. It is
deliberately a table rather than a queue of messages: Celery has no result
backend here (``CELERY_RESULT_BACKEND`` is empty by design), so a chord cannot
gather 504 per-word tasks, and it does not need to — rows already answer "what is
left", "what failed", and "what is a worker holding right now" without a second
piece of infrastructure.

``UNIQUE (job_id, position)`` is what makes redelivery harmless: the plan is
written once, and building only ever *transitions* rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.database import Base
from app.infrastructure.db.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.infrastructure.db.types import UTCDateTime

#: JSONB on Postgres, plain JSON on the SQLite the test suite runs against.
HintPayload = JSON().with_variant(JSONB(), "postgresql")


class DeckBuildJobModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "deck_build_jobs"
    __table_args__ = (Index("ix_deck_build_jobs_slug_state", "template_slug", "state"),)

    template_slug: Mapped[str] = mapped_column(String(80), nullable=False)
    template_version: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    #: sha256 of the template files as read at plan time.
    template_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    #: ``SET NULL`` rather than ``CASCADE``: a deleted deck must not take its
    #: build history — and the record of what it cost — with it.
    deck_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("decks.id", ondelete="SET NULL"), index=True
    )
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="planned")

    #: Pinned at plan time so a mid-build deploy cannot write half the deck under
    #: one prompt version and half under another.
    content_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    native_language: Mapped[str] = mapped_column(String(64), nullable=False, default="Persian")
    register: Mapped[str] = mapped_column(String(16), nullable=False, default="adult")
    #: Copied from the template at plan time, not re-read during the build.
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="general")
    #: Comma-separated strategy names, in order. A short list of known tokens, so
    #: a string beats a join table and beats JSON that nothing queries.
    strategies: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    # Counters. Every one of these is incremented in SQL, never read-then-written
    # — two workers finishing an item at the same moment must not lose a count.
    items_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lexemes_reused: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lexemes_generated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    senses_enriched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ai_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)


class DeckBuildItemModel(UUIDPrimaryKeyMixin, Base):
    """One word of one build.

    No ``TimestampMixin``: ``created_at`` would be identical across every row of
    a plan written in one statement, and ``updated_at`` is maintained explicitly
    here because claims are made with bulk UPDATEs that never load the ORM object.
    """

    __tablename__ = "deck_build_items"
    __table_args__ = (
        # Both the resume key and the dedup key: the plan is written once, and
        # building transitions rows rather than inserting them.
        UniqueConstraint("job_id", "position", name="uq_deck_build_items_position"),
        # The claim query: pending or reclaimable, oldest first.
        Index("ix_deck_build_items_claim", "job_id", "state", "next_attempt_at"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("deck_build_jobs.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_label: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    unit_position: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    #: Exactly as the template wrote it — the diff target against the source.
    source_term: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized: Mapped[str] = mapped_column(String(255), nullable=False)
    #: ``{"part_of_speech": …, "context": …, "gloss": …}``. JSON rather than three
    #: columns because it is opaque to SQL — nothing filters or aggregates on it.
    hint: Mapped[dict[str, Any] | None] = mapped_column(HintPayload)

    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    lexeme_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lexemes.id", ondelete="SET NULL")
    )
    sense_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lexeme_senses.id", ondelete="SET NULL")
    )
    word_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("words.id", ondelete="SET NULL"))

    selection: Mapped[str | None] = mapped_column(String(24))
    selection_score: Mapped[float | None] = mapped_column(Float)

    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    #: One enrichment per item, ever. The guard against paying repeatedly for a
    #: sense the model cannot produce.
    enriched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: When a worker took this row. A claim older than the reclaim window means
    #: that worker is gone, so the row is fair game again.
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
