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
from typing import Any

from app.core.logging import get_logger

logger = get_logger("vocably.rate_limit")


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


class RedisFixedWindowRateLimiter:
    """Shared-state limiting, for the limits that are security controls.

    :class:`SlidingWindowRateLimiter` keeps its counters in process memory, so
    with N workers the real budget is N times the configured one and a restart
    clears it. That is an acceptable backstop against SMS spend. It is the wrong
    primitive for handle enumeration and invite-code guessing, where the budget
    *is* the control.

    A fixed window (INCR + EXPIRE) rather than a sliding one: it is two commands
    and no Lua, and its worst case — twice the budget across a window boundary —
    does not matter for limits set to make guessing infeasible rather than to
    meter precisely.

    **Never fails open.** An unreachable Redis falls back to the in-process
    limiter, so the limit degrades from global to per-worker rather than
    disappearing.
    """

    def __init__(self, redis: Any, window_seconds: int, fallback: SlidingWindowRateLimiter) -> None:
        self._redis = redis
        self._window = window_seconds
        self._fallback = fallback

    async def allow(self, key: str, max_events: int) -> bool:
        try:
            count = await self._redis.incr(key)
            if count == 1:
                await self._redis.expire(key, self._window)
        except Exception as exc:  # noqa: BLE001 — any Redis failure degrades, never opens
            # One line, no traceback: an outage means *every* request takes this
            # path, and a stack trace per request buries the incident it is
            # reporting. The message names the cause well enough to act on.
            logger.warning("rate_limit.redis_unavailable: %s", exc)
            return self._fallback.allow(key, max_events)
        return bool(count <= max_events)
