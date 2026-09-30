"""Ingest one public-domain book, unpublished, and print its chapter list.

    make book-ingest source=standard_ebooks ref=https://standardebooks.org/ebooks/jane-austen/pride-and-prejudice
    make book-ingest source=gutenberg ref=11
    make book-ingest source=upload ref=/path/to/book.epub
    make book-ingest source=gutenberg ref=11 queue=1     # hand it to a worker instead

Equivalent without make: ``python -m app.scripts.ingest_book <source> <ref> [--queue]``.

Runs inline by default: a book is one download and a parse of well under a
second, and the chapter list printed at the end is the review a human does
before ``PATCH /admin/books/{id}/publish``. Nothing here publishes. Exits
non-zero if the source cannot be fetched or the file is not a readable EPUB.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx

from app.application.services.book_ingest_service import BookIngestService
from app.core.database import async_session_factory
from app.core.exceptions import AppError
from app.core.logging import configure_logging, get_logger
from app.domain.enums import BookSource
from app.infrastructure.books.sources import BookFetcher
from app.infrastructure.db.repositories.book_repository import SqlAlchemyBookRepository

logger = get_logger("vocably.ingest_book")


async def ingest(source: BookSource, ref: str) -> int:
    configure_logging()
    async with httpx.AsyncClient() as client, async_session_factory() as session:
        service = BookIngestService(SqlAlchemyBookRepository(session), BookFetcher(client))
        try:
            outcome = await service.ingest(source=source, ref=ref)
        except AppError as exc:
            print(f"error: {exc.message}", file=sys.stderr)
            return 1
        await session.commit()
    book = outcome.book
    print(f"{outcome.action}: {book.title} by {book.author} [{book.id}]")
    print(f"  {book.total_chapters} chapters, {book.total_words} words, public={book.is_public}")
    print(f"  rights: {book.rights or '(none stated: publishing will ask for one)'}")
    for chapter in book.chapters:
        part = f"{chapter.part_title} / " if chapter.part_title else ""
        print(f"  {chapter.index:>3}. {part}{chapter.title}  ({chapter.word_count} words)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest a public-domain book, unpublished.")
    parser.add_argument("source", choices=[s.value for s in BookSource])
    parser.add_argument("ref", help="A Gutenberg number, a Standard Ebooks page URL, or a path.")
    parser.add_argument("--queue", action="store_true", help="Enqueue on a Celery worker.")
    args = parser.parse_args()
    if args.queue:
        from app.tasks.books import ingest_book

        print(f"queued: {ingest_book.delay(args.source, args.ref).id}")
        return 0
    return asyncio.run(ingest(BookSource(args.source), args.ref))


if __name__ == "__main__":
    sys.exit(main())
