"""SQLAlchemy implementation of :class:`DeckActivityRepository`.

Both reads are grouped queries over one deck, deliberately: the roster is the
place this feature would otherwise become thirty round trips, and CLAUDE.md
already forbids user-facing aggregation over ``word_reviews``.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import LeitnerBox
from app.domain.repositories.deck_activity_repository import (
    DeckActivityRepository,
    MemberTotals,
    MemberWeek,
)
from app.infrastructure.db.dialects import upsert_insert
from app.infrastructure.db.models.daily_deck_activity import DailyDeckActivityModel
from app.infrastructure.db.models.word_progress import WordProgressModel

_LEARNING_BOXES = (LeitnerBox.NEW, LeitnerBox.LEARNING, LeitnerBox.FAMILIAR)


class SqlAlchemyDeckActivityRepository(DeckActivityRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_review(
        self, user_id: UUID, deck_id: UUID, day: date, *, mastered: bool
    ) -> None:
        table = DailyDeckActivityModel
        stmt = upsert_insert(self._session)(table).values(
            user_id=user_id, deck_id=deck_id, day=day, reviews=1, mastered=1 if mastered else 0
        )
        # An upsert rather than read-then-write: two grades in the same second
        # would otherwise race, and one learner's review would vanish from the
        # week's count.
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=["user_id", "deck_id", "day"],
                set_={
                    "reviews": table.reviews + 1,
                    "mastered": table.mastered + (1 if mastered else 0),
                },
            )
        )

    async def totals_for_deck(self, deck_id: UUID) -> list[MemberTotals]:
        learning = func.sum(
            case((WordProgressModel.box.in_([int(b) for b in _LEARNING_BOXES]), 1), else_=0)
        )
        mastered = func.sum(case((WordProgressModel.box == int(LeitnerBox.MASTERED), 1), else_=0))
        stmt = (
            select(
                WordProgressModel.user_id,
                func.count().label("seen"),
                func.coalesce(learning, 0).label("learning"),
                func.coalesce(mastered, 0).label("mastered"),
                func.max(WordProgressModel.last_reviewed_at).label("last_active_at"),
            )
            # word_progress.deck_id is the denormalised mirror; without it this
            # would join `words` for every member of every deck.
            .where(WordProgressModel.deck_id == deck_id)
            .group_by(WordProgressModel.user_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            MemberTotals(
                user_id=user_id,
                seen=int(seen),
                learning=int(lrn),
                mastered=int(mst),
                last_active_at=last_active_at,
            )
            for user_id, seen, lrn, mst, last_active_at in rows
        ]

    async def week_for_deck(self, deck_id: UUID, since: date) -> list[MemberWeek]:
        table = DailyDeckActivityModel
        stmt = (
            select(
                table.user_id,
                func.coalesce(func.sum(table.reviews), 0),
                func.coalesce(func.sum(table.mastered), 0),
            )
            .where(table.deck_id == deck_id, table.day >= since)
            .group_by(table.user_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            MemberWeek(user_id=user_id, reviews=int(reviews), mastered=int(mastered))
            for user_id, reviews, mastered in rows
        ]
