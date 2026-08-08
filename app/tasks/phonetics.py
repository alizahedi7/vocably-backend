"""Background fill-in of ``words.phonetic``.

Named ``vocably.ai.*`` so it routes to the AI queue: it is slow, external and
bursty in exactly the way provider calls are, and putting it on the maintenance
queue is how a few hundred dictionary requests delay partition maintenance.

The work itself lives in :class:`PhoneticBackfillService` — this module is an
adapter, the way a router is: build the session and the adapters, call the
service, translate the outcome into a log line.
"""

from __future__ import annotations

from app.application.services.phonetic_backfill_service import (
    BackfillResult,
    PhoneticBackfillService,
)
from app.core.config import settings
from app.core.database import async_session_factory
from app.core.logging import get_logger
from app.infrastructure.db.repositories.word_repository import SqlAlchemyWordRepository
from app.infrastructure.dictionary.factory import dictionary_service
from app.tasks.celery_app import celery_app
from app.tasks.runtime import run_async

logger = get_logger("vocably.tasks.phonetics")


@celery_app.task(
    name="vocably.ai.backfill_phonetics",
    # Idempotent by construction: every write is conditional on the row still
    # holding NULL, so a redelivery after a lost worker re-asks a few terms and
    # overwrites nothing.
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_backoff_max=3600,
    retry_jitter=True,
    max_retries=3,
)
def backfill_phonetics() -> str:
    """Look up the IPA for cards saved without one.

    Does nothing at all when ``DICTIONARY_ENABLED`` is off. That flag is what
    decides whether this deployment talks to a dictionary; a background job
    quietly making the calls the request path is configured not to make would
    make the flag a lie.
    """
    if not settings.dictionary_enabled:
        logger.info("phonetic backfill skipped: DICTIONARY_ENABLED is off")
        return "disabled"
    result = run_async(_run(settings.phonetic_backfill_batch_size))
    if result.exhausted:
        return "up-to-date"
    return f"checked={result.terms_checked} rows={result.words_updated}"


async def _run(limit: int) -> BackfillResult:
    async with async_session_factory() as session:
        service = PhoneticBackfillService(
            SqlAlchemyWordRepository(session),
            dictionary_service(),
        )
        result = await service.run(limit=limit)
        # One commit for the batch. A crash halfway loses at most this run's
        # writes, and the next run simply asks those terms again.
        await session.commit()
        return result
