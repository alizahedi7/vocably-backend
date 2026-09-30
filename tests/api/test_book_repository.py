"""The reader's repositories, against the real models and a real database.

What these pin is the part only a database can prove: that a whole book lands
in one write and reads back in order, that a chapter id cannot be read through
the wrong book, that the progress upsert returns what it just wrote (the
identity map once handed back the *previous* position), and that the cascades
carry the policy — a user's progress dies with them, a book's text with it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import AlreadyExistsError
from app.domain.entities.book import (
    Book,
    BookBlock,
    BookChapter,
    PassageTranslation,
    ReadingPosition,
)
from app.domain.enums import BlockKind, BookSource
from app.infrastructure.db.models.book import BookProgressModel
from app.infrastructure.db.models.user import UserModel
from app.infrastructure.db.repositories.book_repository import (
    SqlAlchemyBookProgressRepository,
    SqlAlchemyBookRepository,
    SqlAlchemyPassageTranslationRepository,
)

from .conftest import UserFactory

Sessions = async_sessionmaker[AsyncSession]


def a_book(source_id: str = "11", *, language: str = "en", marker: str = "") -> Book:
    """Two chapters of three blocks, with the cumulative counts ingest computes."""
    book = Book(
        slug=f"a-book-{source_id}",
        title="A Book",
        author="Ann Author",
        language=language,
        source=BookSource.GUTENBERG,
        source_id=source_id,
        rights="Public domain in the USA.",
        content_hash=f"hash-{source_id}{marker}",
        extra={"subjects": ["Fiction"]},
    )
    words_before_chapter = 0
    for index in range(2):
        chapter = BookChapter(
            book_id=book.id,
            index=index,
            title=f"Chapter {index + 1}{marker}",
            words_before=words_before_chapter,
        )
        for position in range(3):
            chapter.blocks.append(
                BookBlock(
                    chapter_id=chapter.id,
                    position=position,
                    kind=BlockKind.PARAGRAPH,
                    text=f"Chapter {index} block {position} text.{marker}",
                    text_hash=f"h-{source_id}-{index}-{position}{marker}",
                    word_count=10,
                    words_before=10 * position,
                )
            )
        chapter.word_count = 30
        chapter.block_count = 3
        words_before_chapter += 30
        book.chapters.append(chapter)
    book.total_chapters, book.total_words = 2, 60
    return book


async def _create(sessions: Sessions, book: Book, *, public: bool = False) -> Book:
    async with sessions() as session:
        repo = SqlAlchemyBookRepository(session)
        stored = await repo.create(book)
        if public:
            stored = await repo.set_published(stored.id, True) or stored
        await session.commit()
    return stored


# ── Books ─────────────────────────────────────────────────────


async def test_a_book_is_written_whole_and_read_back_in_order(session_factory: Sessions) -> None:
    stored = await _create(session_factory, a_book())
    assert stored.is_public is False  # ingest never publishes
    assert [c.title for c in stored.chapters] == ["Chapter 1", "Chapter 2"]
    assert stored.extra == {"subjects": ["Fiction"]}
    async with session_factory() as session:
        chapter = await SqlAlchemyBookRepository(session).get_chapter(
            stored.id, stored.chapters[1].id
        )
    assert chapter is not None
    assert [b.position for b in chapter.blocks] == [0, 1, 2]
    assert chapter.blocks[2].words_before == 20


async def test_the_same_source_cannot_be_ingested_twice(session_factory: Sessions) -> None:
    await _create(session_factory, a_book("11"))
    with pytest.raises(AlreadyExistsError):
        await _create(session_factory, a_book("11"))


async def test_a_chapter_is_only_readable_through_its_own_book(session_factory: Sessions) -> None:
    first = await _create(session_factory, a_book("1"))
    second = await _create(session_factory, a_book("2"))
    async with session_factory() as session:
        repo = SqlAlchemyBookRepository(session)
        assert await repo.get_chapter(second.id, first.chapters[0].id) is None
        assert await repo.get_chapter(first.id, first.chapters[0].id) is not None


async def test_chapter_blocks_page_by_position(session_factory: Sessions) -> None:
    stored = await _create(session_factory, a_book())
    async with session_factory() as session:
        repo = SqlAlchemyBookRepository(session)
        page = await repo.get_chapter(stored.id, stored.chapters[0].id, after=-1, limit=2)
        rest = await repo.get_chapter(stored.id, stored.chapters[0].id, after=1, limit=2)
    assert page is not None and rest is not None
    assert [b.position for b in page.blocks] == [0, 1]
    assert [b.position for b in rest.blocks] == [2]


async def test_a_block_reads_back_with_its_chapter_and_book(session_factory: Sessions) -> None:
    stored = await _create(session_factory, a_book())
    async with session_factory() as session:
        repo = SqlAlchemyBookRepository(session)
        chapter = await repo.get_chapter(stored.id, stored.chapters[1].id)
        assert chapter is not None
        found = await repo.get_block(chapter.blocks[1].id)
        by_hash = await repo.get_blocks_by_hash(chapter.blocks[1].text_hash)
        assert await repo.get_block(uuid4()) is None
    assert found is not None
    book, found_chapter, block = found
    assert (book.id, found_chapter.index, block.position) == (stored.id, 1, 1)
    assert [b.id for b in by_hash] == [block.id]


async def test_only_published_books_are_listed(session_factory: Sessions) -> None:
    await _create(session_factory, a_book("1"), public=True)
    await _create(session_factory, a_book("2"))
    await _create(session_factory, a_book("3", language="fa"), public=True)
    async with session_factory() as session:
        repo = SqlAlchemyBookRepository(session)
        everything, total = await repo.list_public()
        english, english_total = await repo.list_public(language="en")
    assert total == 2 and len(everything) == 2
    assert english_total == 1 and english[0].source_id == "1"


async def test_publishing_goes_both_ways_and_is_idempotent(session_factory: Sessions) -> None:
    stored = await _create(session_factory, a_book())
    async with session_factory() as session:
        repo = SqlAlchemyBookRepository(session)
        first = await repo.set_published(stored.id, True)
        again = await repo.set_published(stored.id, True)
        hidden = await repo.set_published(stored.id, False)
        missing = await repo.set_published(uuid4(), True)
    assert first is not None and first.published_at is not None
    assert again is not None and again.published_at == first.published_at
    assert hidden is not None and not hidden.is_public and hidden.published_at is None
    assert missing is None


async def test_replacing_a_private_books_text_keeps_its_id(session_factory: Sessions) -> None:
    stored = await _create(session_factory, a_book())
    replacement = a_book(marker=" v2")
    replacement.id = stored.id
    replacement.slug = stored.slug
    for chapter in replacement.chapters:
        chapter.book_id = stored.id
    async with session_factory() as session:
        repo = SqlAlchemyBookRepository(session)
        updated = await repo.replace_content(replacement)
        await session.commit()
    assert updated.id == stored.id
    assert updated.content_hash == "hash-11 v2"
    assert [c.title for c in updated.chapters] == ["Chapter 1 v2", "Chapter 2 v2"]


async def test_rights_can_be_stated_after_ingest(session_factory: Sessions) -> None:
    stored = await _create(session_factory, a_book())
    async with session_factory() as session:
        updated = await SqlAlchemyBookRepository(session).set_rights(stored.id, "CC0 1.0.")
    assert updated is not None and updated.rights == "CC0 1.0."


# ── Progress ──────────────────────────────────────────────────


def _position(
    user_id: object, book: Book, chapter: int, block: int, offset: int
) -> ReadingPosition:
    target = book.chapters[chapter]
    return ReadingPosition(
        user_id=user_id,  # type: ignore[arg-type]
        book_id=book.id,
        chapter_id=target.id,
        block_id=target.blocks[block].id,
        char_offset=offset,
        percent=chapter * 50,
    )


async def test_a_second_sync_in_one_session_returns_the_new_position(
    session_factory: Sessions, make_user: UserFactory
) -> None:
    user = await make_user()
    book = a_book()
    await _create(session_factory, book, public=True)
    async with session_factory() as session:
        repo = SqlAlchemyBookProgressRepository(session)
        first = await repo.upsert(_position(user.id, book, 0, 0, 5))
        second = await repo.upsert(_position(user.id, book, 1, 2, 9))
        await session.commit()
    assert (first.char_offset, first.percent) == (5, 0)
    # The identity map held the first row; without re-reading, this was 5.
    assert (second.char_offset, second.percent, second.block_id) == (
        9,
        50,
        book.chapters[1].blocks[2].id,
    )
    async with session_factory() as session:
        rows = await session.scalar(select(func.count()).select_from(BookProgressModel))
    assert rows == 1


async def test_the_shelf_hides_books_taken_out_of_the_library(
    session_factory: Sessions, make_user: UserFactory
) -> None:
    user = await make_user()
    shown, hidden = a_book("1"), a_book("2")
    await _create(session_factory, shown, public=True)
    await _create(session_factory, hidden, public=True)
    async with session_factory() as session:
        repo = SqlAlchemyBookProgressRepository(session)
        await repo.upsert(_position(user.id, shown, 0, 0, 0))
        await repo.upsert(_position(user.id, hidden, 0, 0, 0))
        await SqlAlchemyBookRepository(session).set_published(hidden.id, False)
        shelf = await repo.list_for_user(user.id)
        await session.commit()
    assert [p.book_id for p in shelf] == [shown.id]


async def test_removing_from_the_shelf_reports_whether_anything_was_there(
    session_factory: Sessions, make_user: UserFactory
) -> None:
    user = await make_user()
    book = a_book()
    await _create(session_factory, book, public=True)
    async with session_factory() as session:
        repo = SqlAlchemyBookProgressRepository(session)
        await repo.upsert(_position(user.id, book, 0, 0, 0))
        assert await repo.delete(user.id, book.id) is True
        assert await repo.delete(user.id, book.id) is False


async def test_progress_is_user_data_and_dies_with_the_user(
    session_factory: Sessions, make_user: UserFactory
) -> None:
    user = await make_user()
    book = a_book()
    await _create(session_factory, book, public=True)
    async with session_factory() as session:
        await SqlAlchemyBookProgressRepository(session).upsert(_position(user.id, book, 0, 0, 0))
        await session.commit()
    async with session_factory() as session:
        await session.execute(delete(UserModel).where(UserModel.id == user.id))
        await session.commit()
        rows = await session.scalar(select(func.count()).select_from(BookProgressModel))
    assert rows == 0


# ── Passage translations ──────────────────────────────────────


async def test_the_first_translation_stored_is_the_one_everyone_reads(
    session_factory: Sessions,
) -> None:
    async with session_factory() as session:
        repo = SqlAlchemyPassageTranslationRepository(session)
        for text in ("first", "second"):
            await repo.put(
                PassageTranslation(
                    text_hash="h", target_language="Persian", prompt_version=1, translation=text
                )
            )
        await session.commit()
    async with session_factory() as session:
        repo = SqlAlchemyPassageTranslationRepository(session)
        hit = await repo.get("h", target_language="Persian", prompt_version=1)
        other_version = await repo.get("h", target_language="Persian", prompt_version=2)
        await repo.get("h", target_language="Persian", prompt_version=1)
        await session.commit()
        counted = await repo.get("h", target_language="Persian", prompt_version=1)
    assert hit is not None and hit.translation == "first"
    assert other_version is None  # a prompt bump retires rows by never matching them
    # Every read counts, the one reporting it included: three reads, three hits.
    assert counted is not None and counted.hit_count == 3
