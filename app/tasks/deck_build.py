"""The deck-build driver: a task that works through a plan and re-queues itself.

Named ``vocably.ai.*`` so ``task_routes`` puts it on the **AI queue**. A 500-word
build makes hundreds of slow outbound calls, and a backlog of those must never
delay partition maintenance.

**Why a self-rescheduling batch task and not fan-out.** The obvious design — one
task per word gathered by a chord — is not available here: ``CELERY_RESULT_BACKEND``
is empty by design, and chords require a result backend. Adding one purely to
coordinate a batch would be real infrastructure for a job that already has a
better coordinator: ``deck_build_items``. Rows also give, for free, a rate limit
(batch size × worker concurrency), trivial resume, and progress that is a
``GROUP BY`` rather than a log scrape.

**Idempotent by construction**, which ``task_acks_late`` requires: every item is
taken with a conditional UPDATE committed before any provider call, so a
redelivered message finds finished work finished and claimed work claimed.
"""

from __future__ import annotations

from typing import cast
from uuid import UUID

from app.application.services.deck_build_service import BatchOutcome, DeckBuildService
from app.application.services.lexicon_service import LexiconService
from app.core.config import settings
from app.core.database import async_session_factory
from app.core.logging import get_logger
from app.infrastructure.ai.factory import (
    configured_model,
    effective_prompt_version,
    lookup_chain,
    raw_ai_provider,
)
from app.infrastructure.ai.lexicon_ai_service import SenseEnricher
from app.infrastructure.db.repositories.deck_build_repository import (
    SqlAlchemyDeckBuildRepository,
)
from app.infrastructure.db.repositories.deck_discovery_repository import (
    SqlAlchemyDeckDiscoveryRepository,
)
from app.infrastructure.db.repositories.deck_member_repository import (
    SqlAlchemyDeckMemberRepository,
)
from app.infrastructure.db.repositories.deck_repository import SqlAlchemyDeckRepository
from app.infrastructure.db.repositories.deck_unit_repository import SqlAlchemyDeckUnitRepository
from app.infrastructure.db.repositories.lexicon_repository import SqlAlchemyLexiconRepository
from app.infrastructure.db.repositories.word_repository import SqlAlchemyWordRepository
from app.tasks.celery_app import celery_app
from app.tasks.runtime import run_async

logger = get_logger("vocably.tasks.deckbuild")


@celery_app.task(
    name="vocably.ai.build_deck",
    bind=True,
    # Retries here cover the *driver* failing — the database being unreachable,
    # the job row vanishing. Individual words never reach this: an item that
    # fails is recorded with its own attempt count and backoff, because retrying
    # the task would re-punish the words in the batch that succeeded.
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)
def build_deck(self: object, job_id: str) -> str:
    """Resolve one batch of a build, then queue the next while work remains."""
    outcome = run_async(_run_batch(UUID(job_id), settings.deck_build_batch_size))

    if outcome.has_more:
        build_deck.apply_async(
            (job_id,),
            countdown=settings.deck_build_batch_delay_seconds,
        )
        return f"{outcome.summary()} more=yes"

    finished = outcome.finished_state.value if outcome.finished_state else "idle"
    return f"{outcome.summary()} state={finished}"


async def _run_batch(job_id: UUID, limit: int) -> BatchOutcome:
    async with async_session_factory() as session:
        lexicon = LexiconService(
            SqlAlchemyLexiconRepository(session),
            content_version=effective_prompt_version(),
            provider=settings.ai_provider,
            model=configured_model(),
        )
        service = DeckBuildService(
            SqlAlchemyDeckBuildRepository(session),
            SqlAlchemyDeckRepository(session),
            SqlAlchemyDeckMemberRepository(session),
            SqlAlchemyDeckUnitRepository(session),
            SqlAlchemyWordRepository(session),
            SqlAlchemyDeckDiscoveryRepository(session),
            lexicon,
            lookup_chain(session, lexicon),
            content_version=effective_prompt_version(),
            # The raw adapter: the wrappers above it have nothing to add to a
            # call that is explicitly asking for what the lexicon does not hold.
            enricher=cast("SenseEnricher", raw_ai_provider()),
            claim_timeout_seconds=settings.deck_build_claim_timeout_seconds,
        )
        outcome = await service.run_batch(job_id, limit=limit)
        # One commit for the batch's bookkeeping. The claims inside it were
        # already committed separately — that is what makes them claims — so a
        # crash here loses decisions, not work already published.
        await session.commit()
        logger.info("build %s batch: %s", job_id, outcome.summary())
        return outcome
