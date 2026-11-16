"""Hand the request's database connection back before every provider call.

A request's ``AsyncSession`` checks a pooled connection out at its first query —
``get_current_user`` reads the user — and holds it, idle in transaction, until
the response is sent. Under that rule a cold lookup kept one of the pool's
connections for the whole 2–40 s the gateway took, so a burst of uncached words
or story requests exhausted the pool and every other request in the app — a
review, a deck list — queued behind it until ``pool_timeout`` turned it into a
500. The deck builder already lives by the rule this restores: no transaction is
held open across the network.

``release`` is the session's ``commit``. Committing ends the transaction and
returns the connection; the next query (the cache write after the answer)
checks one out again for a few milliseconds. Every read before the provider call
is already done and nothing written so far needs to be atomic with what follows,
so splitting the transaction here loses nothing.

Delegation is by ``__getattr__`` rather than by listing methods, deliberately:
``FailoverAIService`` has to name all seven of the provider's methods and warns
that missing one loses failover silently; missing one here would silently hold
the connection again. Every coroutine method is wrapped, whatever it is called.

**The caller must be the only user of the session while a call is in flight.**
``AsyncSession`` refuses a commit concurrent with another operation, which is
why ``ReaderService`` reads and writes its sentence memo outside the window in
which its lookup task runs.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Awaitable, Callable
from typing import Any


class ConnectionReleasingAIService:
    def __init__(self, inner: object, release: Callable[[], Awaitable[None]]) -> None:
        self._inner = inner
        self._release = release

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not inspect.iscoroutinefunction(attr):
            return attr

        @functools.wraps(attr)
        async def released(*args: Any, **kwargs: Any) -> Any:
            await self._release()
            return await attr(*args, **kwargs)

        return released
