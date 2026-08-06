"""One learner's study state against one card.

Split off ``words`` so a deck can be shared as *the same deck*: thirty students
hold thirty rows against one ``words.id``. Rows are written only by ``grade`` —
never fanned out when a deck is shared — so a missing row is the normal state
for a word nobody has opened, and reads substitute box 1 / due now.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.infrastructure.db.models.mixins import TimestampMixin
from app.infrastructure.db.types import UTCDateTime


class WordProgressModel(TimestampMixin, Base):
    __tablename__ = "word_progress"
    __table_args__ = (
        # The due-queue query, moved here from ix_words_user_due.
        Index("ix_word_progress_user_due", "user_id", "due_at"),
        # The per-deck aggregates: the home screen's deck list and the roster.
        Index("ix_word_progress_user_deck_due", "user_id", "deck_id", "due_at"),
        # The roster groups by deck across all its members, so it leads with
        # deck_id rather than user_id.
        Index("ix_word_progress_deck_user", "deck_id", "user_id"),
        CheckConstraint("box BETWEEN 1 AND 5", name="ck_word_progress_box"),
    )

    #: CASCADE: erasing an account erases that person's boxes and nothing else.
    #: A member leaving must never delete a class's cards, which is precisely
    #: why this cascade lands here and not on ``words``.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    #: CASCADE: deleting a card takes everyone's progress on it with it.
    word_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("words.id", ondelete="CASCADE"), primary_key=True
    )
    #: Live mirror of ``words.deck_id`` so per-deck aggregates and the roster
    #: never join ``words``. Kept in step by SqlAlchemyWordRepository.update
    #: when a card moves deck. Deliberately unlike ``word_reviews.deck_id``,
    #: which is frozen at review time — do not unify the two.
    deck_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("decks.id", ondelete="CASCADE"), nullable=False
    )

    box: Mapped[int] = mapped_column(default=1, nullable=False)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    review_count: Mapped[int] = mapped_column(default=0, nullable=False)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    # Review summary counters, maintained by WordProgress.apply_review on the
    # UPDATE the grade already issues. Redundant with `word_reviews` by design —
    # they keep "hardest words" and time-to-mastery off the event log entirely.
    lapse_count: Mapped[int] = mapped_column(default=0, nullable=False)
    consecutive_correct: Mapped[int] = mapped_column(default=0, nullable=False)
    first_reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    mastered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    #: ReviewGrade.ordinal of the most recent grade; NULL until first reviewed.
    last_grade: Mapped[int | None] = mapped_column(SmallInteger())
