"""SQLAlchemy implementations of the three reader ports.

Whole-book writes go through one ``add_all`` per table, so a two-thousand-block
novel is a handful of round trips rather than thousands. A chapter read is one
query for the chapter and one for its blocks in ``position`` order — the unique
index on ``(chapter_id, position)`` exists for exactly that statement.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import ColumnElement, CursorResult, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError
from app.domain.entities.book import (
    Book,
    BookBlock,
    BookChapter,
    PassageTranslation,
    ReadingPosition,
)
from app.domain.repositories.book_repository import (
    BookProgressRepository,
    BookRepository,
    PassageTranslationRepository,
)
from app.infrastructure.db import mappers
from app.infrastructure.db.dialects import upsert_insert
from app.infrastructure.db.models.book import (
    BookBlockModel,
    BookChapterModel,
    BookModel,
    BookProgressModel,
    PassageTranslationModel,
)

# ── Books ─────────────────────────────────────────────────────


class SqlAlchemyBookRepository(BookRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_public(
        self, *, language: str | None = None, limit: int = 20, offset: int = 0
    ) -> tuple[list[Book], int]:
        where: list[ColumnElement[bool]] = [BookModel.is_public.is_(True)]
        if language:
            where.append(BookModel.language == language)
        total = (
            await self._session.execute(select(func.count()).select_from(BookModel).where(*where))
        ).scalar_one()
        rows = (
            await self._session.execute(
                select(BookModel)
                .where(*where)
                .order_by(BookModel.published_at.desc().nullslast(), BookModel.title)
                .limit(limit)
                .offset(offset)
            )
        ).scalars()
        return [mappers.book_to_entity(m) for m in rows], int(total)

    async def get(self, book_id: UUID) -> Book | None:
        # ``populate_existing``: this is also the read-back after a write in the
        # same session, where the identity map holds a row whose server-set
        # timestamps are expired — touching one would lazy-load, which async
        # SQLAlchemy refuses (MissingGreenlet). Re-reading is one indexed query.
        stmt = select(BookModel).where(BookModel.id == book_id)
        model = (
            (await self._session.execute(stmt.execution_options(populate_existing=True)))
            .scalars()
            .first()
        )
        if model is None:
            return None
        chapters = (
            await self._session.execute(
                select(BookChapterModel)
                .where(BookChapterModel.book_id == book_id)
                .order_by(BookChapterModel.index)
            )
        ).scalars()
        return mappers.book_to_entity(model, [mappers.book_chapter_to_entity(c) for c in chapters])

    async def get_by_source(self, source: str, source_id: str) -> Book | None:
        stmt = select(BookModel).where(BookModel.source == source, BookModel.source_id == source_id)
        model = (await self._session.execute(stmt)).scalars().first()
        return mappers.book_to_entity(model) if model else None

    async def get_chapter(
        self, book_id: UUID, chapter_id: UUID, *, after: int = -1, limit: int = 400
    ) -> BookChapter | None:
        stmt = select(BookChapterModel).where(
            BookChapterModel.id == chapter_id, BookChapterModel.book_id == book_id
        )
        chapter = (await self._session.execute(stmt)).scalars().first()
        if chapter is None:
            return None
        blocks = (
            await self._session.execute(
                select(BookBlockModel)
                .where(BookBlockModel.chapter_id == chapter_id, BookBlockModel.position > after)
                .order_by(BookBlockModel.position)
                .limit(limit)
            )
        ).scalars()
        return mappers.book_chapter_to_entity(
            chapter, [mappers.book_block_to_entity(b) for b in blocks]
        )

    async def get_block(self, block_id: UUID) -> tuple[Book, BookChapter, BookBlock] | None:
        row = (
            await self._session.execute(
                select(BookBlockModel, BookChapterModel, BookModel)
                .join(BookChapterModel, BookChapterModel.id == BookBlockModel.chapter_id)
                .join(BookModel, BookModel.id == BookChapterModel.book_id)
                .where(BookBlockModel.id == block_id)
            )
        ).first()
        if row is None:
            return None
        block, chapter, book = row
        return (
            mappers.book_to_entity(book),
            mappers.book_chapter_to_entity(chapter),
            mappers.book_block_to_entity(block),
        )

    async def get_blocks_by_hash(self, text_hash: str) -> Sequence[BookBlock]:
        stmt = select(BookBlockModel).where(BookBlockModel.text_hash == text_hash).limit(5)
        return [
            mappers.book_block_to_entity(b) for b in (await self._session.execute(stmt)).scalars()
        ]

    async def create(self, book: Book) -> Book:
        self._session.add(
            BookModel(
                id=book.id,
                slug=book.slug[:160],
                title=book.title[:300],
                author=book.author[:300],
                language=book.language[:16],
                description=book.description,
                cover_url=book.cover_url[:500],
                source=book.source.value,
                source_id=book.source_id[:300],
                source_url=book.source_url[:500],
                rights=book.rights,
                extra=book.extra,
                content_hash=book.content_hash,
                total_chapters=book.total_chapters,
                total_words=book.total_words,
                is_public=False,
            )
        )
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise AlreadyExistsError("That book has already been ingested.") from exc
        await self._write_text(book)
        return await self._reload(book.id)

    async def replace_content(self, book: Book) -> Book:
        # Chapters cascade to blocks, and progress rows cascade with them —
        # acceptable only because the service guarantees the book is private.
        await self._session.execute(
            delete(BookChapterModel).where(BookChapterModel.book_id == book.id)
        )
        await self._session.execute(
            update(BookModel)
            .where(BookModel.id == book.id)
            .values(
                title=book.title[:300],
                author=book.author[:300],
                description=book.description,
                cover_url=book.cover_url[:500],
                rights=book.rights,
                extra=book.extra,
                content_hash=book.content_hash,
                total_chapters=book.total_chapters,
                total_words=book.total_words,
            )
        )
        await self._write_text(book)
        return await self._reload(book.id)

    async def set_published(self, book_id: UUID, is_public: bool) -> Book | None:
        model = await self._session.get(BookModel, book_id)
        if model is None:
            return None
        if is_public and not model.is_public:
            model.published_at = datetime.now(UTC)
        if not is_public:
            model.published_at = None
        model.is_public = is_public
        await self._session.flush()
        return await self.get(book_id)

    async def set_rights(self, book_id: UUID, rights: str) -> Book | None:
        model = await self._session.get(BookModel, book_id)
        if model is None:
            return None
        model.rights = rights
        await self._session.flush()
        return await self.get(book_id)

    async def _write_text(self, book: Book) -> None:
        self._session.add_all(
            [
                BookChapterModel(
                    id=c.id,
                    book_id=book.id,
                    index=c.index,
                    title=c.title[:300],
                    part_title=c.part_title[:300],
                    word_count=c.word_count,
                    block_count=c.block_count,
                    words_before=c.words_before,
                )
                for c in book.chapters
            ]
        )
        await self._session.flush()
        self._session.add_all(
            [
                BookBlockModel(
                    id=b.id,
                    chapter_id=c.id,
                    position=b.position,
                    kind=b.kind.value,
                    text=b.text,
                    text_hash=b.text_hash,
                    word_count=b.word_count,
                    words_before=b.words_before,
                )
                for c in book.chapters
                for b in c.blocks
            ]
        )
        await self._session.flush()

    async def _reload(self, book_id: UUID) -> Book:
        stored = await self.get(book_id)
        if stored is None:  # pragma: no cover — written in this transaction
            raise RuntimeError(f"book {book_id} vanished during write")
        return stored


# ── Progress ──────────────────────────────────────────────────


class SqlAlchemyBookProgressRepository(BookProgressRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: UUID, book_id: UUID) -> ReadingPosition | None:
        # ``populate_existing`` because ``upsert`` reads back through here, and
        # a Core upsert does not refresh an object already in the identity map:
        # the second sync of a session would otherwise return the first.
        stmt = select(BookProgressModel).where(
            BookProgressModel.user_id == user_id, BookProgressModel.book_id == book_id
        )
        result = await self._session.execute(stmt.execution_options(populate_existing=True))
        model = result.scalars().first()
        return mappers.book_progress_to_entity(model) if model else None

    async def list_for_user(self, user_id: UUID, *, limit: int = 20) -> list[ReadingPosition]:
        rows = (
            await self._session.execute(
                select(BookProgressModel)
                .join(BookModel, BookModel.id == BookProgressModel.book_id)
                # A book taken out of the library leaves the shelf with it.
                .where(BookProgressModel.user_id == user_id, BookModel.is_public.is_(True))
                .order_by(BookProgressModel.updated_at.desc())
                .limit(limit)
            )
        ).scalars()
        return [mappers.book_progress_to_entity(m) for m in rows]

    async def upsert(self, position: ReadingPosition) -> ReadingPosition:
        values = {
            "user_id": position.user_id,
            "book_id": position.book_id,
            "chapter_id": position.chapter_id,
            "block_id": position.block_id,
            "char_offset": position.char_offset,
            "percent": position.percent,
            "updated_at": position.updated_at,
        }
        stmt = upsert_insert(self._session)(BookProgressModel).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "book_id"],
            set_={k: v for k, v in values.items() if k not in ("user_id", "book_id")},
        )
        await self._session.execute(stmt)
        stored = await self.get(position.user_id, position.book_id)
        if stored is None:  # pragma: no cover — upserted in this transaction
            raise RuntimeError("reading position vanished during upsert")
        return stored

    async def delete(self, user_id: UUID, book_id: UUID) -> bool:
        result = await self._session.execute(
            delete(BookProgressModel).where(
                BookProgressModel.user_id == user_id, BookProgressModel.book_id == book_id
            )
        )
        # The async wrapper's static type is Result; the runtime object is a
        # CursorResult, which is where rowcount lives.
        return bool(cast("CursorResult[Any]", result).rowcount)


# ── Passage translations ──────────────────────────────────────


class SqlAlchemyPassageTranslationRepository(PassageTranslationRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self, text_hash: str, *, target_language: str, prompt_version: int
    ) -> PassageTranslation | None:
        key = (text_hash, target_language, prompt_version)
        model = await self._session.get(PassageTranslationModel, key)
        if model is None:
            return None
        # Counted in SQL, never read-then-written: two readers of one paragraph
        # in the same second must both count.
        await self._session.execute(
            update(PassageTranslationModel)
            .where(
                PassageTranslationModel.text_hash == text_hash,
                PassageTranslationModel.target_language == target_language,
                PassageTranslationModel.prompt_version == prompt_version,
            )
            .values(hit_count=PassageTranslationModel.hit_count + 1)
        )
        return PassageTranslation(
            text_hash=model.text_hash,
            target_language=model.target_language,
            prompt_version=model.prompt_version,
            translation=model.translation,
            provider=model.provider,
            model=model.model,
            hit_count=model.hit_count,
            created_at=model.created_at,
        )

    async def put(self, translation: PassageTranslation) -> None:
        stmt = upsert_insert(self._session)(PassageTranslationModel).values(
            text_hash=translation.text_hash,
            target_language=translation.target_language[:64],
            prompt_version=translation.prompt_version,
            translation=translation.translation,
            provider=translation.provider[:32],
            model=translation.model[:128],
        )
        # DO NOTHING: the first translation stored is the one everyone reads, so
        # a race between two readers cannot flap the text between calls.
        await self._session.execute(
            stmt.on_conflict_do_nothing(
                index_elements=["text_hash", "target_language", "prompt_version"]
            )
        )
