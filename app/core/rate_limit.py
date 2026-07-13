"""In-process sliding-window rate limiting.

State lives in process memory: with multiple workers the effective budget is
multiplied by the worker count, and it resets on restart. That is acceptable as
a cost-abuse backstop; strict global limits belong in the fronting proxy/WAF.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from threading import Lock


class SlidingWindowRateLimiter:
    """Allow at most N events per key within a rolling window.

    The per-event cap is passed to :meth:`allow` (not the constructor) so it can
    follow live settings without rebuilding the limiter.
    """

    def __init__(
        self,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window = window_seconds
        self._clock = clock
        self._events: dict[str, deque[float]] = {}
        self._lock = Lock()
        self._last_sweep = clock()

    def allow(self, key: str, max_events: int) -> bool:
        """Record one event for ``key`` and report whether it fit the budget."""
        now = self._clock()
        with self._lock:
            self._sweep(now)
            events = self._events.setdefault(key, deque())
            while events and now - events[0] >= self._window:
                events.popleft()
            if len(events) >= max_events:
                return False
            events.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._events.clear()

    def _sweep(self, now: float) -> None:
        # Drop keys whose events have all expired, or idle IPs would pin a
        # deque in memory forever. At most once per window; caller holds the lock.
        if now - self._last_sweep < self._window:
            return
        self._last_sweep = now
        dead = [
            key
            for key, events in self._events.items()
            if not events or now - events[-1] >= self._window
        ]
        for key in dead:
            del self._events[key]
