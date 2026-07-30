"""Celery entry point — background and scheduled work.

``app.tasks.celery_app:celery_app`` is the application object the worker and beat
processes are pointed at (see the Makefile). Importing this package registers
every task module, so ``celery -A app.tasks`` finds them all.
"""

from app.tasks.celery_app import celery_app

__all__ = ["celery_app"]
