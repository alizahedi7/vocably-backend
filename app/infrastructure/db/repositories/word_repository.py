"""SQLAlchemy implementation of :class:`WordRepository`."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.word import Word
from app.domain.repositories.word_repository import WordRepository
from app.infrastructure.db import mappers
from app.infrastructure.db.models.word import WordModel
from app.infrastructure.db.models.word_progress import WordProgressModel


class SqlAlchemyWordRepository(WordRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, word_id: UUID) -> Word | None:
        model = await self._session.get(WordModel, word_id)
        return mappers.word_to_entity(model) if model else None

    async def list_in_deck(
        self,
        deck_id: UUID,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Word]:
        stmt = select(WordModel).where(WordModel.deck_id == deck_id)
        # id as tie-breaker: created_at is second-granular, so bulk inserts would
        # otherwise make page boundaries non-deterministic.
        stmt = stmt.order_by(WordModel.created_at.desc(), WordModel.id.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        if offset:
            stmt = stmt.offset(offset)
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
        moved_deck = model.deck_id != word.deck_id
        mappers.apply_word(word, model)
        await self._session.flush()
        if moved_deck:
            # word_progress.deck_id is a live mirror of this column, and every
            # per-deck aggregate reads it rather than joining `words`. One
            # statement, bounded by the number of members who studied the card.
            await self._session.execute(
                update(WordProgressModel)
                .where(WordProgressModel.word_id == word.id)
                .values(deck_id=word.deck_id)
            )
        await self._session.refresh(model)
        return mappers.word_to_entity(model)

    async def delete(self, word_id: UUID) -> None:
        await self._session.execute(delete(WordModel).where(WordModel.id == word_id))
