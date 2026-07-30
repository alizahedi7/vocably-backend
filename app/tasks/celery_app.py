"""The Celery application: background and scheduled work.

This is a second entry point into the same codebase, alongside the FastAPI app in
[app/api]. Tasks are adapters in exactly the way routers are: they unpack a
message, call into the application layer, and translate the outcome back. Domain
and application code stays unaware that Celery exists.

Run the two processes with::

    make worker     # executes tasks
    make beat       # emits scheduled tasks; EXACTLY ONE instance, ever

``beat`` is a clock, not a worker. Two beat processes mean every scheduled task
fires twice, so it must never be scaled past one replica — unlike the worker,
which scales freely.

Queues are split by *what would be blocked* rather than by feature. Maintenance
is small, quick and must happen on time; AI work is slow, external and bursty.
On one shared queue a backlog of the latter delays the former, which is how a
partition-maintenance job silently stops running.
"""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.schedules import crontab
from celery.signals import setup_logging

from app.core.config import settings
from app.core.logging import configure_logging

#: Queue for scheduled upkeep: small, fast, latency-sensitive.
QUEUE_MAINTENANCE = "maintenance"
#: Queue reserved for AI work — provider calls are slow, rate-limited and bursty,
#: and must not be able to starve maintenance.
QUEUE_AI = "ai"
QUEUE_DEFAULT = "default"

#: Task modules the worker imports on startup. A task that is not reachable from
#: this list is not registered, and beat scheduling it fails with "unregistered
#: task" at runtime — so every new task module must be added here.
TASK_MODULES = [
    "app.tasks.maintenance",
]

celery_app = Celery("vocably", include=TASK_MODULES)

celery_app.conf.update(
    broker_url=settings.celery_broker_url,
    # Empty means "no result backend": nothing waits on these tasks' return
    # values, and storing them would grow Redis forever for no reader.
    result_backend=settings.celery_result_backend or None,
    # Schedules are defined and interpreted in UTC. Local time would silently
    # shift every scheduled run twice a year on DST boundaries.
    timezone="UTC",
    enable_utc=True,
    task_default_queue=QUEUE_DEFAULT,
    task_routes={
        "vocably.maintenance.*": {"queue": QUEUE_MAINTENANCE},
        "vocably.ai.*": {"queue": QUEUE_AI},
    },
    # Acknowledge only after the task finishes, so work is redelivered rather
    # than lost if a worker is killed mid-task (deploys, OOM, spot instances).
    # This makes retries possible, which means tasks must be safe to run twice
    # — see the idempotence note on each task.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # With acks_late, a worker that hoards prefetched messages holds them
    # hostage while it works. One at a time keeps the queue redistributable.
    worker_prefetch_multiplier=1,
    task_time_limit=settings.celery_task_time_limit_seconds,
    task_soft_time_limit=settings.celery_task_soft_time_limit_seconds,
    task_track_started=True,
    # Drop scheduled runs that piled up while the worker was down instead of
    # replaying a week of them at once on restart.
    broker_transport_options={"visibility_timeout": 3600},
    beat_schedule={
        "review-partitions-daily": {
            "task": "vocably.maintenance.review_partitions",
            "schedule": crontab(hour=settings.review_history_maintenance_hour, minute=0),
            "options": {"queue": QUEUE_MAINTENANCE, "expires": 12 * 3600},
        },
    },
)


@setup_logging.connect
def _configure_celery_logging(**_kwargs: Any) -> None:
    """Use the app's logging configuration instead of Celery's own.

    Connecting to this signal at all is what stops Celery from replacing the
    root handlers, so worker output matches the API's format and ships to the
    same place.
    """
    configure_logging()
