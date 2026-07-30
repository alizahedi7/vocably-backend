"""Bridge between Celery's synchronous workers and this codebase's async I/O.

Celery's prefork worker calls tasks as ordinary blocking functions, while every
repository and service here is ``async``. Each task therefore needs an event loop
to run its work on, and that is where the one real trap lives:

``asyncio.run`` creates a **new** event loop and destroys it on return. SQLAlchemy's
async engine, however, is process-global and holds a pool of asyncpg connections
that are bound to the loop that opened them. Left alone, the second task in a
worker process would fetch a pooled connection belonging to the first task's
now-dead loop and hang or fail in ways that look like random database flakiness.

So the engine is disposed at the end of every task run: the pool is emptied, and
the next task opens fresh connections on its own loop. The cost is one connection
handshake per task, which is irrelevant for background work and buys correctness
that is otherwise very hard to debug. Tasks that turn into a high-rate stream
should get their own engine with a ``NullPool`` instead of paying attention here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine

from app.core.database import engine


def run_async[T](work: Coroutine[object, object, T]) -> T:
    """Run ``work`` to completion on a fresh event loop, then release the pool."""

    async def _run() -> T:
        try:
            return await work
        finally:
            # Inside the loop on purpose: closing asyncpg connections is itself
            # async, and must happen before the loop it belongs to goes away.
            await engine.dispose()

    return asyncio.run(_run())
