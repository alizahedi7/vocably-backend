"""Ingest: fetch a public-domain book, parse it, and store it unpublished.

The counterpart of ``DeckBuildService`` for books, and much simpler, because no
token is spent: a book is one download and one CPU-bound parse (0.05-0.15 s
for a novel), so it is one transaction rather than a resumable plan.

Three rules, in order of how much they matter:

1. **Ingest never publishes.** A book lands with ``is_public = false`` and an
   admin reads the chapter list before ``PATCH /admin/books/{id}/publish``.
   The parser is heuristic on Gutenberg input — a mis-detected heading level
   turns forty chapters into one — and a glance at the table of contents is
   the cheapest possible test.
2. **Idempotent by content.** Re-ingesting the same file is a no-op that
   returns the stored book. A *different* file for an unpublished book
   replaces its text; for a published book it is refused with 409, because
   reading positions and lookups point at block ids in the text people have.
3. **Rights are stated, not assumed.** ``books.rights`` records the source's
   own statement. An upload carries none, and publishing it is refused until
   an operator supplies one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.domain.entities.book import Book, BookBlock, BookChapter
from app.domain.enums import BookSource
from app.domain.repositories.book_repository import BookRepository
from app.infrastructure.books.epub import (
    EpubParseError,
    ParsedBook,
    parse_epub,
    text_hash,
    word_count,
)
from app.infrastructure.books.sources import FetchedBook

logger = get_logger("vocably.books.ingest")

_SLUG_NOISE = re.compile(r"[^a-z0-9]+")


class EbookFetcher(Protocol):
    """What ingest needs from a source: ``BookFetcher`` in production."""

    async def fetch(self, *, source: BookSource, ref: str) -> FetchedBook: ...


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    book: Book
    #: ``created`` | ``unchanged`` | ``replaced``. What the operator sees.
    action: str


class BookIngestService:
    def __init__(self, books: BookRepository, fetcher: EbookFetcher) -> None:
        self._books = books
        self._fetcher = fetcher

    async def ingest(self, *, source: BookSource, ref: str) -> IngestOutcome:
        fetched = await self._fetcher.fetch(source=source, ref=ref)
        try:
            parsed = parse_epub(fetched.data)
        except EpubParseError as exc:
            raise ValidationError(f"Could not read that ebook: {exc}") from exc

        book = materialise(parsed, fetched)
        existing = await self._books.get_by_source(fetched.source.value, fetched.source_id)
        if existing is None:
            stored = await self._books.create(book)
            logger.info(
                "ingested %r: %d chapters, %d words",
                stored.title,
                stored.total_chapters,
                stored.total_words,
            )
            return IngestOutcome(stored, "created")
        if existing.content_hash == book.content_hash:
            return IngestOutcome(existing, "unchanged")
        if existing.is_public:
            raise ConflictError(
                f"{existing.title!r} is published and its text differs from this file. "
                "Unpublish it first, or ingest the new edition as a separate upload."
            )
        book.id = existing.id
        book.slug = existing.slug
        return IngestOutcome(await self._books.replace_content(book), "replaced")

    async def publish(self, book_id: UUID, *, is_public: bool, rights: str = "") -> Book:
        """Flip visibility, both ways. A book with no rights line cannot go public."""
        book = await self._books.get(book_id)
        if book is None:
            raise NotFoundError("Book not found.")
        if rights.strip() and rights.strip() != book.rights:
            await self._books.set_rights(book_id, rights.strip())
        elif is_public and not book.rights.strip():
            raise ValidationError(
                "State the rights under which this text may be served before publishing it."
            )
        updated = await self._books.set_published(book_id, is_public)
        if updated is None:
            raise NotFoundError("Book not found.")
        return updated


def materialise(parsed: ParsedBook, fetched: FetchedBook) -> Book:
    """Entities with ids, hashes and cumulative word counts, ready to insert."""
    book = Book(
        id=uuid4(),
        slug=_slug(parsed.title, fetched.source_id),
        title=parsed.title or "Untitled",
        author=parsed.author,
        language=parsed.language or "en",
        description=parsed.description,
        cover_url=fetched.cover_url,
        source=fetched.source,
        source_id=fetched.source_id,
        source_url=fetched.source_url,
        rights=fetched.rights or parsed.rights,
        extra=dict(fetched.extra),
        content_hash=parsed.content_hash,
    )
    words_before_chapter = 0
    for index, chapter in enumerate(parsed.chapters):
        entity = BookChapter(
            id=uuid4(),
            book_id=book.id,
            index=index,
            title=chapter.title[:300],
            part_title=chapter.part_title[:300],
            words_before=words_before_chapter,
        )
        words_before_block = 0
        for position, block in enumerate(chapter.blocks):
            count = word_count(block.text)
            entity.blocks.append(
                BookBlock(
                    id=uuid4(),
                    chapter_id=entity.id,
                    position=position,
                    kind=block.kind,
                    text=block.text,
                    text_hash=text_hash(block.text),
                    word_count=count,
                    words_before=words_before_block,
                )
            )
            words_before_block += count
        entity.word_count = words_before_block
        entity.block_count = len(entity.blocks)
        words_before_chapter += entity.word_count
        book.chapters.append(entity)
    book.total_chapters = len(book.chapters)
    book.total_words = words_before_chapter
    return book


def _slug(title: str, source_id: str) -> str:
    base = _SLUG_NOISE.sub("-", title.casefold()).strip("-")[:120] or "book"
    # The source id tells two editions of one title apart.
    tail = _SLUG_NOISE.sub("", source_id.casefold())[-12:] or "0"
    return f"{base}-{tail}"
