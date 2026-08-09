"""Bridge between Celery's synchronous workers and this codebase's async I/O.

Celery's prefork worker calls tasks as ordinary blocking functions, while every
repository and service here is ``async``. Each task therefore needs an event loop
to run its work on, and that is where the one real trap lives:

``asyncio.run`` creates a **new** event loop and destroys it on return. The pools
this app holds — SQLAlchemy's asyncpg connections, Redis clients, HTTP clients —
are all **process-global and bound to the loop that opened them**. Left alone,
the second task in a worker process fetches a connection belonging to the first
task's now-dead loop and fails in ways that look like random flakiness.

So every loop-bound pool is released at the end of every task run, and the next
task opens fresh ones on its own loop. The cost is a handful of handshakes per
task, irrelevant for background work, and it buys correctness that is otherwise
very hard to debug.

**How this failure hides.** The database pool fails loudly. The Redis and HTTP
pools do not: every call through them is deliberately best-effort, so a
``RuntimeError`` from a dead loop is caught, logged at INFO, and degrades to "no
cache" — and, for the dictionary, to "no dictionary at all", which silently drops
a deck build off the grounded path and onto full generation. It was found exactly
that way: a production build whose first batch was grounded and whose every later
batch was not.

Tasks that turn into a high-rate stream should get their own engine with a
``NullPool`` instead of paying attention here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from app.core.database import engine
from app.core.logging import get_logger
from app.infrastructure.ai.factory import single_flight
from app.infrastructure.dictionary.factory import dictionary_service

logger = get_logger("vocably.tasks.runtime")


#: The memoized, loop-bound resources to release after each task run. Every entry
#: is an ``lru_cache``-wrapped factory whose value holds a connection pool.
#: Adding a new process-wide async client anywhere means adding it here, or its
#: second use in a worker fails — quietly, if that client is best-effort.
_POOLED_FACTORIES: tuple[Any, ...] = (dictionary_service, single_flight)


def run_async[T](work: Coroutine[object, object, T]) -> T:
    """Run ``work`` to completion on a fresh event loop, then release every pool."""

    async def _run() -> T:
        try:
            return await work
        finally:
            # Inside the loop on purpose: closing these connections is itself
            # async, and must happen before the loop they belong to goes away.
            await _release_pools()

    return asyncio.run(_run())


async def _release_pools() -> None:
    await engine.dispose()
    for factory in _POOLED_FACTORIES:
        await _release(factory)


async def _release(factory: Any) -> None:
    """Close one memoized factory's resource and forget it.

    Reads the cache before calling: an empty cache means this task never touched
    the resource, and calling the factory to close it would *build* a client
    purely to throw it away.
    """
    cache_info = getattr(factory, "cache_info", None)
    if cache_info is None or not cache_info().currsize:
        return
    resource = factory()
    try:
        if resource is not None:
            await _aclose(resource)
    except Exception:  # noqa: BLE001 — teardown must never fail a completed task
        logger.warning("failed to release %s", getattr(factory, "__name__", factory))
    finally:
        # Cleared last, so the next task builds a client on its own loop even if
        # closing this one went wrong.
        factory.cache_clear()


async def _aclose(resource: object) -> None:
    closer = getattr(resource, "aclose", None)
    if closer is not None:
        await closer()
