"""SQLAlchemy implementation of :class:`LexiconRepository`.

Every insert goes through :meth:`_insert_or_skip`, a savepoint around one
statement. Same reasoning as the lookup cache's ``_insert``: it runs identically
on the Postgres of production and the SQLite the default test suite uses, and a
losing race rolls back only that statement rather than the caller's transaction —
which, during a deck build, may already hold the card that was just written.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, distinct, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.core.logging import get_logger
from app.domain.entities.lexeme import Lexeme, LexemeSense
from app.domain.enums import SenseStatus
from app.domain.repositories.lexicon_repository import LexiconRepository, LexiconStats
from app.infrastructure.db import mappers
from app.infrastructure.db.models.lexicon import (
    LexemeModel,
    LexemeSenseModel,
    LexemeSenseTranslationModel,
)

logger = get_logger("vocably.lexicon")


class SqlAlchemyLexiconRepository(LexiconRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Reads ─────────────────────────────────────────────────

    async def get(self, lemma: str, *, language: str = "en") -> Lexeme | None:
        stmt = select(LexemeModel).where(
            LexemeModel.lemma == lemma, LexemeModel.language == language
        )
        model = (await self._session.execute(stmt)).scalars().first()
        if model is None:
            return None
        return await self._hydrate(model)

    async def get_by_id(self, lexeme_id: UUID) -> Lexeme | None:
        model = await self._session.get(LexemeModel, lexeme_id)
        return await self._hydrate(model) if model else None

    async def get_sense(self, sense_id: UUID) -> LexemeSense | None:
        model = await self._session.get(LexemeSenseModel, sense_id)
        if model is None:
            return None
        translations = await self._translations_for([sense_id])
        return mappers.lexeme_sense_to_entity(model, translations.get(sense_id, []))

    # ── Writes ────────────────────────────────────────────────

    async def upsert(
        self,
        lemma: str,
        *,
        language: str = "en",
        display_term: str,
        phonetic: str | None = None,
    ) -> Lexeme:
        existing = await self.get(lemma, language=language)
        if existing is not None:
            if phonetic is not None and existing.phonetic is None:
                await self.set_phonetic(existing.id, phonetic)
                existing.phonetic = phonetic
            return existing

        model = LexemeModel(
            lemma=lemma[:255],
            language=language[:16],
            display_term=(display_term or lemma)[:255],
            phonetic=phonetic[:200] if phonetic is not None else None,
        )
        if await self._insert_or_skip(model):
            return mappers.lexeme_to_entity(model)

        # Another writer created it between the read and the insert — the normal
        # outcome of a lookup and a build reaching a new word together. Adopt it.
        adopted = await self.get(lemma, language=language)
        if adopted is None:  # pragma: no cover — only reachable if it was deleted meanwhile
            raise RuntimeError(f"lexeme {lemma!r} vanished during upsert")
        return adopted

    async def add_senses(self, lexeme_id: UUID, senses: list[LexemeSense]) -> list[LexemeSense]:
        stored: list[LexemeSense] = []
        for sense in senses:
            model = LexemeSenseModel(
                lexeme_id=lexeme_id,
                sense_key=sense.sense_key[:80],
                register=sense.register[:16],
                position=sense.position,
                part_of_speech=sense.part_of_speech[:32],
                context=sense.context[:120],
                definition=sense.definition,
                example=sense.example,
                status=sense.status.value,
                content_version=sense.content_version,
                provider=sense.provider[:32],
                model=sense.model[:128],
                source=sense.source.value,
            )
            if await self._insert_or_skip(model):
                sense_id = model.id
            else:
                # The sense_key was already taken. Keep the row that is there:
                # it may carry a human's approval, and this write is a machine's.
                existing = await self._sense_by_key(lexeme_id, sense.sense_key, sense.register)
                if existing is None:  # pragma: no cover — deleted between the two statements
                    continue
                sense_id = existing.id

            for translation in sense.translations:
                await self._add_translation(sense_id, translation.native_language, translation)

            refreshed = await self.get_sense(sense_id)
            if refreshed is not None:
                stored.append(refreshed)
        return stored

    async def add_translation_if_absent(
        self,
        sense_id: UUID,
        *,
        native_language: str,
        native_meaning: str,
        content_version: int,
    ) -> bool:
        if not native_meaning.strip():
            return False
        model = LexemeSenseTranslationModel(
            sense_id=sense_id,
            native_language=native_language[:64],
            native_meaning=native_meaning,
            status=SenseStatus.AUTO.value,
            content_version=content_version,
        )
        # The unique constraint decides, not a prior read: two learners with the
        # same new native language arriving together is the ordinary race.
        return await self._insert_or_skip(model)

    async def set_phonetic(self, lexeme_id: UUID, phonetic: str) -> None:
        # `is_(None)` in the predicate is the whole point: '' is a real answer
        # meaning "this word has no IPA", and a later lookup that also found
        # none must not be able to overwrite anything.
        stmt = (
            update(LexemeModel)
            .where(LexemeModel.id == lexeme_id, LexemeModel.phonetic.is_(None))
            .values(phonetic=phonetic[:200])
        )
        await self._session.execute(stmt)

    async def update_sense(
        self,
        sense_id: UUID,
        *,
        status: SenseStatus | None = None,
        definition: str | None = None,
        example: str | None = None,
        context: str | None = None,
        native_language: str | None = None,
        native_meaning: str | None = None,
    ) -> LexemeSense | None:
        model = await self._session.get(LexemeSenseModel, sense_id)
        if model is None:
            return None
        if status is not None:
            model.status = status.value
        if definition is not None:
            model.definition = definition
        if example is not None:
            model.example = example
        if context is not None:
            model.context = context[:120]
            # sense_key is derived from (part of speech, context), so an edited
            # label has to move with it or enrichment would later "discover" the
            # sense it already has under the old key.
            from app.domain.entities.lexeme import sense_key_for

            model.sense_key = sense_key_for(model.part_of_speech, model.context)[:80]
        model.updated_at = datetime.now(UTC)

        if native_language and native_meaning is not None:
            await self._upsert_translation(sense_id, native_language, native_meaning)

        await self._session.flush()
        return await self.get_sense(sense_id)

    # ── Admin reads ───────────────────────────────────────────

    async def search(
        self,
        *,
        q: str | None = None,
        status: SenseStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Lexeme], int]:
        stmt: Select[tuple[LexemeModel]] = select(LexemeModel)
        count_stmt = select(func.count(distinct(LexemeModel.id))).select_from(LexemeModel)

        if status is not None:
            join_on = LexemeSenseModel.lexeme_id == LexemeModel.id
            stmt = stmt.join(LexemeSenseModel, join_on).where(
                LexemeSenseModel.status == status.value
            )
            count_stmt = count_stmt.join(LexemeSenseModel, join_on).where(
                LexemeSenseModel.status == status.value
            )
        if q:
            needle = f"%{q.strip().casefold()}%"
            predicate = or_(
                LexemeModel.lemma.like(needle),
                func.lower(LexemeModel.display_term).like(needle),
            )
            stmt = stmt.where(predicate)
            count_stmt = count_stmt.where(predicate)

        total = int((await self._session.execute(count_stmt)).scalar_one())
        stmt = stmt.distinct().order_by(LexemeModel.lemma.asc()).limit(limit).offset(offset)
        models = (await self._session.execute(stmt)).scalars().unique().all()
        return [await self._hydrate(m) for m in models], total

    async def list_senses_by_status(
        self,
        status: SenseStatus,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[tuple[Lexeme, LexemeSense]], int]:
        base = (
            select(LexemeSenseModel, LexemeModel)
            .join(LexemeModel, LexemeModel.id == LexemeSenseModel.lexeme_id)
            .where(LexemeSenseModel.status == status.value)
        )
        total = int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(LexemeSenseModel)
                    .where(LexemeSenseModel.status == status.value)
                )
            ).scalar_one()
        )
        stmt = (
            base.order_by(LexemeSenseModel.created_at.asc(), LexemeSenseModel.id.asc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(stmt)).all()
        sense_ids = [row[0].id for row in rows]
        translations = await self._translations_for(sense_ids)
        return [
            (
                mappers.lexeme_to_entity(row[1]),
                mappers.lexeme_sense_to_entity(row[0], translations.get(row[0].id, [])),
            )
            for row in rows
        ], total

    async def stats(self, *, current_version: int) -> LexiconStats:
        # One grouped query for the statuses rather than four counts: the table
        # is small today and will not stay that way.
        by_status = {
            str(row[0]): int(row[1])
            for row in (
                await self._session.execute(
                    select(LexemeSenseModel.status, func.count()).group_by(LexemeSenseModel.status)
                )
            ).all()
        }
        lexeme_count = await self._session.execute(select(func.count()).select_from(LexemeModel))
        lexemes = int(lexeme_count.scalar_one())
        translations = int(
            (
                await self._session.execute(
                    select(func.count()).select_from(LexemeSenseTranslationModel)
                )
            ).scalar_one()
        )
        stale = int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(LexemeSenseModel)
                    .where(
                        LexemeSenseModel.content_version < current_version,
                        # Approved content is never stale: a human blessed that
                        # wording, and a prompt edit does not un-bless it.
                        LexemeSenseModel.status != SenseStatus.APPROVED.value,
                    )
                )
            ).scalar_one()
        )
        return LexiconStats(
            lexemes=lexemes,
            senses=sum(by_status.values()),
            translations=translations,
            needs_review=by_status.get(SenseStatus.NEEDS_REVIEW.value, 0),
            rejected=by_status.get(SenseStatus.REJECTED.value, 0),
            approved=by_status.get(SenseStatus.APPROVED.value, 0),
            stale=stale,
        )

    # ── Helpers ───────────────────────────────────────────────

    async def _hydrate(self, model: LexemeModel) -> Lexeme:
        stmt = (
            select(LexemeSenseModel)
            .where(LexemeSenseModel.lexeme_id == model.id)
            .order_by(LexemeSenseModel.position.asc(), LexemeSenseModel.id.asc())
        )
        sense_models = (await self._session.execute(stmt)).scalars().all()
        translations = await self._translations_for([s.id for s in sense_models])
        senses = [
            mappers.lexeme_sense_to_entity(s, translations.get(s.id, [])) for s in sense_models
        ]
        return mappers.lexeme_to_entity(model, senses)

    async def _translations_for(
        self, sense_ids: list[UUID]
    ) -> dict[UUID, list[LexemeSenseTranslationModel]]:
        """One query for every sense's translations — never one per sense."""
        if not sense_ids:
            return {}
        stmt = select(LexemeSenseTranslationModel).where(
            LexemeSenseTranslationModel.sense_id.in_(sense_ids)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        grouped: dict[UUID, list[LexemeSenseTranslationModel]] = {}
        for row in rows:
            grouped.setdefault(row.sense_id, []).append(row)
        return grouped

    async def _sense_by_key(
        self, lexeme_id: UUID, sense_key: str, register: str
    ) -> LexemeSenseModel | None:
        stmt = select(LexemeSenseModel).where(
            LexemeSenseModel.lexeme_id == lexeme_id,
            LexemeSenseModel.sense_key == sense_key,
            LexemeSenseModel.register == register,
        )
        return (await self._session.execute(stmt)).scalars().first()

    async def _add_translation(self, sense_id: UUID, language: str, translation: object) -> None:
        native_meaning = getattr(translation, "native_meaning", "")
        if not native_meaning:
            return
        model = LexemeSenseTranslationModel(
            sense_id=sense_id,
            native_language=language[:64],
            native_meaning=native_meaning,
            status=getattr(translation, "status", SenseStatus.AUTO).value,
            content_version=getattr(translation, "content_version", 0),
        )
        await self._insert_or_skip(model)

    async def _upsert_translation(self, sense_id: UUID, language: str, meaning: str) -> None:
        stmt = select(LexemeSenseTranslationModel).where(
            LexemeSenseTranslationModel.sense_id == sense_id,
            LexemeSenseTranslationModel.native_language == language,
        )
        existing = (await self._session.execute(stmt)).scalars().first()
        if existing is not None:
            existing.native_meaning = meaning
            existing.updated_at = datetime.now(UTC)
            await self._session.flush()
            return
        self._session.add(
            LexemeSenseTranslationModel(
                sense_id=sense_id,
                native_language=language[:64],
                native_meaning=meaning,
                status=SenseStatus.APPROVED.value,
            )
        )
        await self._session.flush()

    async def _insert_or_skip(self, model: DeclarativeBase) -> bool:
        """Insert inside a savepoint; ``False`` when a concurrent writer won.

        ``DO NOTHING`` semantics, expressed portably. Never ``DO UPDATE``: the
        row already there may have been approved by a human, and this writer is
        a machine that has no idea.
        """
        try:
            async with self._session.begin_nested():
                self._session.add(model)
                await self._session.flush()
        except IntegrityError:
            return False
        return True
