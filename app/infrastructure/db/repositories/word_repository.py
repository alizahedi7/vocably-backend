"""SQLAlchemy implementation of :class:`WordRepository`."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.word import Word
from app.domain.enums import LeitnerBox
from app.domain.repositories.word_repository import WordRepository
from app.infrastructure.db import mappers
from app.infrastructure.db.models.word import WordModel


class SqlAlchemyWordRepository(WordRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, word_id: UUID) -> Word | None:
        model = await self._session.get(WordModel, word_id)
        return mappers.word_to_entity(model) if model else None

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        deck_id: UUID | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Word]:
        stmt = select(WordModel).where(WordModel.user_id == user_id)
        if deck_id is not None:
            stmt = stmt.where(WordModel.deck_id == deck_id)
        # id as tie-breaker: created_at is second-granular, so bulk inserts would
        # otherwise make page boundaries non-deterministic.
        stmt = stmt.order_by(WordModel.created_at.desc(), WordModel.id.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        if offset:
            stmt = stmt.offset(offset)
        models = (await self._session.execute(stmt)).scalars().all()
        return [mappers.word_to_entity(m) for m in models]

    async def list_due(
        self,
        user_id: UUID,
        now: datetime,
        *,
        deck_id: UUID | None = None,
        limit: int | None = None,
    ) -> list[Word]:
        stmt = select(WordModel).where(
            WordModel.user_id == user_id,
            WordModel.due_at <= now,
        )
        if deck_id is not None:
            stmt = stmt.where(WordModel.deck_id == deck_id)
        stmt = stmt.order_by(WordModel.due_at.asc())
        if limit is not None:
            stmt = stmt.limit(limit)
        models = (await self._session.execute(stmt)).scalars().all()
        return [mappers.word_to_entity(m) for m in models]

    async def add(self, word: Word) -> Word:
        model = WordModel(id=word.id)
        mappers.apply_word(word, model)
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return mappers.word_to_entity(model)

    async def update(self, word: Word) -> Word:
        model = await self._session.get(WordModel, word.id)
        if model is None:
            raise ValueError(f"Word {word.id} does not exist")
        mappers.apply_word(word, model)
        await self._session.flush()
        await self._session.refresh(model)
        return mappers.word_to_entity(model)

    async def delete(self, word_id: UUID) -> None:
        await self._session.execute(delete(WordModel).where(WordModel.id == word_id))

    async def box_distribution(self, user_id: UUID) -> dict[LeitnerBox, int]:
        stmt = (
            select(WordModel.box, func.count())
            .where(WordModel.user_id == user_id)
            .group_by(WordModel.box)
        )
        rows = (await self._session.execute(stmt)).all()
        return {LeitnerBox(box): count for box, count in rows}

    async def deck_totals(self, user_id: UUID) -> dict[UUID, tuple[int, int]]:
        stmt = (
            select(
                WordModel.deck_id,
                func.count(),
                func.coalesce(func.sum(WordModel.box), 0),
            )
            .where(WordModel.user_id == user_id)
            .group_by(WordModel.deck_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {deck_id: (int(count), int(box_sum)) for deck_id, count, box_sum in rows}

    async def deck_due_counts(self, user_id: UUID, now: datetime) -> dict[UUID, int]:
        stmt = (
            select(WordModel.deck_id, func.count())
            .where(WordModel.user_id == user_id, WordModel.due_at <= now)
            .group_by(WordModel.deck_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {deck_id: int(count) for deck_id, count in rows}
