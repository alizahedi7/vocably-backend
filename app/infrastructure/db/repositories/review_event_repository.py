"""SQLAlchemy implementation of :class:`ReviewEventRepository`."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.review_event import ReviewEvent
from app.domain.repositories.review_event_repository import ReviewEventRepository
from app.infrastructure.db import mappers
from app.infrastructure.db.models.word_review import WordReviewModel


class SqlAlchemyReviewEventRepository(ReviewEventRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: ReviewEvent) -> None:
        # A Core INSERT rather than session.add(): the ORM would register the
        # row in the identity map and flush it with a RETURNING round trip to
        # populate its id, neither of which is wanted for a write-only append.
        await self._session.execute(
            insert(WordReviewModel).values(mappers.review_event_values(event))
        )

    async def list_for_word(self, word_id: UUID, *, limit: int = 100) -> Sequence[ReviewEvent]:
        stmt = (
            select(WordReviewModel)
            .where(WordReviewModel.word_id == word_id)
            # id as tie-breaker: two reviews of one card can share a timestamp
            # (a fast double-tap), and the page order must still be total.
            .order_by(WordReviewModel.reviewed_at.desc(), WordReviewModel.id.desc())
            .limit(limit)
        )
        models = (await self._session.execute(stmt)).scalars().all()
        return [mappers.review_event_to_entity(m) for m in models]
