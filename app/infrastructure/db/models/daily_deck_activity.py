"""Per-learner, per-deck, per-day review counters — a rollup, not a log.

The roster shows every member's activity this week. Computing that by scanning
``word_reviews`` would break the rule in CLAUDE.md that no user-facing request
aggregates over the event log, and a roster of thirty students would do it
thirty times.

So the counters are incremented on the same UPDATE the grade already issues,
and the roster reads one indexed row per member per day.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DailyDeckActivityModel(Base):
    __tablename__ = "daily_deck_activity"
    __table_args__ = (
        # The roster query: one deck, one week, every member.
        Index("ix_daily_deck_activity_deck_day", "deck_id", "day"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    deck_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("decks.id", ondelete="CASCADE"), primary_key=True
    )
    #: The learner's *local* calendar day, resolved through their timezone at
    #: write time — see app/domain/services/calendar.py. Stored as a date so a
    #: week is a range scan rather than a timezone conversion per row.
    day: Mapped[date] = mapped_column(Date, primary_key=True)

    reviews: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Of those, the ones the scheduler had actually asked for. What makes
    #: "they cleared today's queue" answerable without re-deriving a past day's
    #: dueness, which reviewing a card destroys.
    due_reviews: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Incremented only on the transition *into* box 5, so a learner who lapses
    #: out and returns is not counted twice.
    mastered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
