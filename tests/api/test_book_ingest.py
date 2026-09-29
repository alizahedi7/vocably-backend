"""Ingest, end to end through the parser and the real repository.

The rules pinned: ingest never publishes; the same file twice is a no-op; a new
file replaces a private book's text but is refused for a published one, whose
block ids are promises to every reader holding a position in it; and a book
with no stated rights cannot be published.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.services.book_ingest_service import BookIngestService, materialise
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.domain.entities.book import Book
from app.domain.enums import BookSource
from app.infrastructure.books.epub import parse_epub
from app.infrastructure.books.sources import FetchedBook
from app.infrastructure.db.repositories.book_repository import SqlAlchemyBookRepository
from tests.epub_builder import build_epub, gutenberg_book, standard_ebooks_book

Sessions = async_sessionmaker[AsyncSession]


class FakeFetcher:
    """Hands back whatever file the test last put on the shelf."""

    def __init__(self, data: bytes, *, rights: str = "Public domain in the USA.") -> None:
        self.data = data
        self.rights = rights

    async def fetch(self, *, source: BookSource, ref: str) -> FetchedBook:
        return FetchedBook(
            source=source, source_id=ref, source_url="", data=self.data, rights=self.rights
        )


async def _ingest(sessions: Sessions, fetcher: FakeFetcher, ref: str = "11") -> tuple[str, Book]:
    async with sessions() as session:
        service = BookIngestService(SqlAlchemyBookRepository(session), fetcher)
        outcome = await service.ingest(source=BookSource.GUTENBERG, ref=ref)
        await session.commit()
    return outcome.action, outcome.book


async def test_a_new_book_lands_unpublished_with_its_chapters(session_factory: Sessions) -> None:
    action, book = await _ingest(session_factory, FakeFetcher(build_epub(gutenberg_book())))
    assert action == "created"
    assert book.is_public is False
    assert [c.title for c in book.chapters] == [
        "CHAPTER I.",
        "CHAPTER II.",
        "CHAPTER III.",
    ]


async def test_ingesting_the_same_file_again_changes_nothing(session_factory: Sessions) -> None:
    fetcher = FakeFetcher(build_epub(gutenberg_book()))
    _, first = await _ingest(session_factory, fetcher)
    action, again = await _ingest(session_factory, fetcher)
    assert action == "unchanged"
    assert again.id == first.id


async def test_a_new_file_replaces_a_private_books_text(session_factory: Sessions) -> None:
    fetcher = FakeFetcher(build_epub(gutenberg_book()))
    _, first = await _ingest(session_factory, fetcher)
    fetcher.data = build_epub(standard_ebooks_book(chapters=2))
    action, replaced = await _ingest(session_factory, fetcher)
    assert action == "replaced"
    assert replaced.id == first.id
    assert replaced.total_chapters == 2


async def test_a_published_books_text_is_never_replaced(session_factory: Sessions) -> None:
    fetcher = FakeFetcher(build_epub(gutenberg_book()))
    _, first = await _ingest(session_factory, fetcher)
    async with session_factory() as session:
        await SqlAlchemyBookRepository(session).set_published(first.id, True)
        await session.commit()
    fetcher.data = build_epub(standard_ebooks_book(chapters=2))
    with pytest.raises(ConflictError, match="published"):
        await _ingest(session_factory, fetcher)


async def test_a_file_that_is_not_an_epub_is_a_validation_error(session_factory: Sessions) -> None:
    with pytest.raises(ValidationError, match="Could not read"):
        await _ingest(session_factory, FakeFetcher(b"<html>not an ebook</html>"))


async def test_a_book_without_stated_rights_cannot_be_published(session_factory: Sessions) -> None:
    fetcher = FakeFetcher(build_epub(gutenberg_book()), rights="")
    _, book = await _ingest(session_factory, fetcher)
    async with session_factory() as session:
        service = BookIngestService(SqlAlchemyBookRepository(session), fetcher)
        with pytest.raises(ValidationError, match="rights"):
            await service.publish(book.id, is_public=True)
        published = await service.publish(
            book.id,
            is_public=True,
            rights="Public domain in the USA.",
        )
        # Taking a book down never needs a rights statement.
        hidden = await service.publish(book.id, is_public=False)
        with pytest.raises(NotFoundError):
            await service.publish(uuid4(), is_public=True)
    assert published.is_public and published.rights == "Public domain in the USA."
    assert not hidden.is_public


def test_materialise_computes_cumulative_word_counts() -> None:
    parsed = parse_epub(build_epub(gutenberg_book()))
    book = materialise(
        parsed,
        FetchedBook(source=BookSource.GUTENBERG, source_id="11", source_url="", data=b""),
    )
    assert book.total_words == sum(c.word_count for c in book.chapters)
    assert book.chapters[1].words_before == book.chapters[0].word_count
    for chapter in book.chapters:
        assert [b.position for b in chapter.blocks] == list(range(chapter.block_count))
        running = 0
        for block in chapter.blocks:
            assert block.words_before == running
            running += block.word_count
    # A position is a percentage from these numbers alone: the words before the
    # block, plus the share of the block already read.
    first, last = book.chapters[0], book.chapters[-1]
    assert book.percent_at(first, first.blocks[0]) == 0
    start_of_last = last.words_before + last.blocks[-1].words_before
    assert book.percent_at(last, last.blocks[-1]) == round(start_of_last * 100 / book.total_words)
    assert book.percent_at(last, last.blocks[-1], len(last.blocks[-1].text)) == 100
    assert book.slug.startswith("a-test-book-")
