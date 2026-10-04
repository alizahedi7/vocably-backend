"""Port: a budget of events per key, for use cases that must throttle themselves."""

from __future__ import annotations

from typing import Protocol


class RateLimiter(Protocol):
    """Most limits here are route dependencies in ``app.api.deps``. This exists
    for the ones that cannot be: a limit keyed by something only the use case
    knows, or whose position *between* two steps is itself the control.
    """

    async def allow(self, key: str, max_events: int) -> bool:
        """Record one event for ``key`` and report whether it fit the budget."""
        ...
