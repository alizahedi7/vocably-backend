"""Word (flashcard) ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.infrastructure.db.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.infrastructure.db.types import UTCDateTime


class WordModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "words"
    __table_args__ = (
        # The due-queue query filters by user + due_at; index it.
        Index("ix_words_user_due", "user_id", "due_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    deck_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("decks.id", ondelete="CASCADE"), index=True, nullable=False
    )

    term: Mapped[str] = mapped_column(String(255), nullable=False)
    meaning: Mapped[str] = mapped_column(Text, nullable=False)
    definition: Mapped[str | None] = mapped_column(Text)
    example: Mapped[str | None] = mapped_column(Text)
    sense_label: Mapped[str | None] = mapped_column(String(120))

    box: Mapped[int] = mapped_column(default=1, nullable=False)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    review_count: Mapped[int] = mapped_column(default=0, nullable=False)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    # Review summary counters, maintained by Word.apply_review on the UPDATE the
    # grade already issues. Redundant with `word_reviews` by design — they keep
    # "hardest words" and time-to-mastery off the event log entirely.
    lapse_count: Mapped[int] = mapped_column(default=0, nullable=False)
    consecutive_correct: Mapped[int] = mapped_column(default=0, nullable=False)
    first_reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    mastered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    #: ReviewGrade.ordinal of the most recent grade; NULL until first reviewed.
    last_grade: Mapped[int | None] = mapped_column(SmallInteger())
