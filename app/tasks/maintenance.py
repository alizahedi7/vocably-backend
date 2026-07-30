"""Scheduled database upkeep."""

from __future__ import annotations

from app.core.config import settings
from app.core.logging import get_logger
from app.scripts.partition_word_reviews import maintain
from app.tasks.celery_app import celery_app
from app.tasks.runtime import run_async

logger = get_logger("vocably.tasks.maintenance")


@celery_app.task(
    name="vocably.maintenance.review_partitions",
    # The work is idempotent (partitions are created IF NOT EXISTS, drops are
    # IF EXISTS), so a redelivery after a lost worker simply repeats it.
    # Retries are spaced out because nothing here is urgent to the minute: the
    # partition window runs a year ahead, so even a full day of failures is
    # slack, not an incident.
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_backoff_max=3600,
    retry_jitter=True,
    max_retries=5,
)
def review_partitions() -> str:
    """Roll the ``word_reviews`` partition window forward.

    Runs the same ``maintain()`` the ``make partitions`` CLI runs — one
    implementation, so the scheduled job and an operator at a terminal cannot
    drift apart.

    Pruning follows ``REVIEW_HISTORY_AUTO_PRUNE`` and is off by default; while it
    is off, expired partitions are reported in the logs but never dropped. A
    background job that deletes learners' history unprompted should be a
    deliberate choice, not something switched on by installing a scheduler.

    Raises on a non-zero result so the failure surfaces as a failed task rather
    than a log line nobody reads. The one non-zero case that is *not* a crash is
    a non-empty default partition, which means maintenance has not run in a very
    long time and needs a human.
    """
    prune = settings.review_history_auto_prune
    exit_code = run_async(maintain(prune=prune))
    if exit_code != 0:
        raise RuntimeError(
            "word_reviews partition maintenance reported a problem "
            f"(exit code {exit_code}); see the preceding log lines."
        )
    return "pruned" if prune else "created"
