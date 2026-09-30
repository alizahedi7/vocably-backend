"""Books in the background: ingest one, and pre-warm a published one.

Ingest is named ``vocably.books.*``, so it lands on the **default** queue: it is
neither maintenance (it is slow and not on a clock) nor AI (it spends no tokens
and must not sit behind a deck build). Idempotent by construction — a
redelivered message re-fetches the same file, finds the same content hash and
returns ``unchanged`` — which is what ``task_acks_late`` requires.

Warming is ``vocably.ai.*``: every cold word is a provider call, and a backlog
of those must never delay partition maintenance.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import httpx

from app.application.services.book_ingest_service import BookIngestService
from app.core.config import settings
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


@celery_app.task(
    name="vocably.ai.warm_book",
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)
def warm_book(book_id: str, offset: int = 0) -> str:
    """Look up one slice of a book's vocabulary, then queue the next slice.

    Named ``vocably.ai.*`` so it lands on the AI queue: every cold lemma is a
    provider call. A slice per run keeps each run well inside Celery's time
    limit, and the slice is a sorted list computed from the book itself, so a
    redelivered or re-run message repeats no work — a lemma already in the
    cache or the lexicon costs an indexed read.
    """
    from app.application.services.book_warm_service import BookWarmService
    from app.infrastructure.nlp.simplemma_lemmatizer import SimplemmaLemmatizer

    async def _plan() -> Any:
        async with async_session_factory() as session:
            service = BookWarmService(SqlAlchemyBookRepository(session), SimplemmaLemmatizer())
            return await service.plan(
                UUID(book_id), offset=offset, batch=settings.reader_warm_batch_size
            )

    plan = run_async(_plan())
    if plan.lemmas:
        run_async(_warm(plan.lemmas))
    logger.info(
        "warm book %s: %d-%d of %d lemmas",
        book_id,
        plan.offset,
        plan.offset + len(plan.lemmas),
        plan.total,
    )
    if plan.next_offset is not None:
        warm_book.apply_async((book_id, plan.next_offset), countdown=1)
        return f"{plan.offset + len(plan.lemmas)}/{plan.total} more=yes"
    return f"{plan.total}/{plan.total} done"


async def _warm(lemmas: list[str]) -> None:
    """Send each lemma through the request path's lookup chain, a few at a time.

    One session per in-flight lookup: a session cannot run two statements at
    once, and the chain writes to the cache and the lexicon as it goes. The
    lexicon's unique constraints make a race between two lemmas' writes a
    no-op rather than a duplicate.
    """
    from app.application.ports.ai_service import LearnerContext
    from app.application.services.lexicon_service import LexiconService
    from app.core.exceptions import ExternalServiceError
    from app.infrastructure.ai.factory import (
        configured_model,
        effective_prompt_version,
        grounded_ai_provider,
        lookup_chain,
    )
    from app.infrastructure.db.repositories.lexicon_repository import SqlAlchemyLexiconRepository

    learner = LearnerContext(native_language=settings.reader_warm_native_language)
    gate = asyncio.Semaphore(settings.reader_warm_concurrency)

    async def one(lemma: str) -> None:
        async with gate, async_session_factory() as session:
            lexicon = LexiconService(
                SqlAlchemyLexiconRepository(session),
                content_version=effective_prompt_version(),
                provider=settings.ai_provider,
                model=configured_model(),
            )
            chain = lookup_chain(session, lexicon, provider=grounded_ai_provider())
            try:
                await chain.look_up_meanings(lemma, learner)
                await session.commit()
            except ExternalServiceError as exc:
                # One word the gateway would not answer is not a reason to stop
                # the other thousands; a learner's tap will try it again.
                logger.warning("warm: %r not answered: %s", lemma, exc)

    await asyncio.gather(*(one(lemma) for lemma in lemmas))
