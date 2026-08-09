"""SQLAlchemy implementation of :class:`WordRepository`."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, delete, func, select, update
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

    async def list_by_ids(self, word_ids: Sequence[UUID]) -> list[Word]:
        if not word_ids:
            return []
        stmt = select(WordModel).where(WordModel.id.in_(word_ids))
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

    # ── phonetic backfill ────────────────────────────────────
    #: The key both backfill statements group and match on. Trimmed as well as
    #: lowercased because the API strips a term on the way in but rows written
    #: before it did are still here, and an untrimmed duplicate would cost a
    #: second request for an answer already in hand.
    _TERM_KEY = func.trim(func.lower(WordModel.term))

    async def list_terms_missing_phonetic(self, limit: int) -> list[str]:
        stmt = (
            select(self._TERM_KEY)
            .where(WordModel.phonetic.is_(None))
            .group_by(self._TERM_KEY)
            # Random, not oldest-first. A term the dictionary has no entry for
            # stays NULL forever (an outage and a miss are indistinguishable
            # through the port, and marking a word "has no IPA" because the
            # dictionary was down would be permanent), so any stable ordering
            # lets a handful of unknown words sit at the head of the queue and
            # consume every run. Sampling instead means each run makes progress.
            .order_by(func.random())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def set_phonetic_for_term(self, term: str, phonetic: str) -> int:
        result = await self._session.execute(
            update(WordModel)
            .where(
                self._TERM_KEY == term.strip().lower(),  # noqa: SIM300 — SQL expression
                # Never clobber a transcription the card arrived with, and never
                # re-answer a term another worker has already answered.
                WordModel.phonetic.is_(None),
            )
            .values(phonetic=phonetic)
        )
        # `execute` is typed as returning a Result; an UPDATE always yields a
        # CursorResult, which is where rowcount lives.
        return cast("CursorResult[Any]", result).rowcount or 0
