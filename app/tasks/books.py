"""Ingest a book in the background: one download, one parse, one transaction.

Named ``vocably.books.*``, so it lands on the **default** queue: it is neither
maintenance (it is slow and not on a clock) nor AI (it spends no tokens and
must not sit behind a deck build). Idempotent by construction — a redelivered
message re-fetches the same file, finds the same content hash and returns
``unchanged`` — which is what ``task_acks_late`` requires.
"""

from __future__ import annotations

import httpx

from app.application.services.book_ingest_service import BookIngestService
from app.core.database import async_session_factory
from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger
from app.domain.enums import BookSource
from app.infrastructure.books.sources import BookFetcher
from app.infrastructure.db.repositories.book_repository import SqlAlchemyBookRepository
from app.tasks.celery_app import celery_app
from app.tasks.runtime import run_async

logger = get_logger("vocably.tasks.books")


@celery_app.task(
    name="vocably.books.ingest",
    # Only an unreachable source is worth retrying. A file that is not an
    # EPUB, or a published book whose text changed, fails the same way twice.
    autoretry_for=(ExternalServiceError,),
    retry_backoff=60,
    retry_backoff_max=900,
    retry_jitter=True,
    max_retries=3,
)
def ingest_book(source: str, ref: str) -> str:
    """Fetch, parse and store one book, unpublished. Returns its id."""
    return run_async(_ingest(BookSource(source), ref))


async def _ingest(source: BookSource, ref: str) -> str:
    async with httpx.AsyncClient() as client, async_session_factory() as session:
        service = BookIngestService(SqlAlchemyBookRepository(session), BookFetcher(client))
        outcome = await service.ingest(source=source, ref=ref)
        await session.commit()
    logger.info("book %s: %s (%s)", outcome.book.id, outcome.action, outcome.book.title)
    return str(outcome.book.id)
