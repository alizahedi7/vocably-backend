"""Sliding-window rate limiter used for OTP abuse protection."""

from __future__ import annotations

from app.core.rate_limit import (
    RedisFixedWindowRateLimiter,
    SlidingWindowRateLimiter,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_allows_up_to_the_budget_then_blocks() -> None:
    limiter = SlidingWindowRateLimiter(window_seconds=60.0, clock=FakeClock())

    assert all(limiter.allow("ip", max_events=3) for _ in range(3))
    assert limiter.allow("ip", max_events=3) is False


def test_budget_frees_up_as_the_window_slides() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(window_seconds=60.0, clock=clock)

    assert limiter.allow("ip", max_events=1)
    assert limiter.allow("ip", max_events=1) is False

    clock.now = 60.0
    assert limiter.allow("ip", max_events=1)


def test_keys_have_independent_budgets() -> None:
    limiter = SlidingWindowRateLimiter(window_seconds=60.0, clock=FakeClock())

    assert limiter.allow("first", max_events=1)
    assert limiter.allow("first", max_events=1) is False
    assert limiter.allow("second", max_events=1)


def test_reset_clears_all_state() -> None:
    limiter = SlidingWindowRateLimiter(window_seconds=60.0, clock=FakeClock())

    assert limiter.allow("ip", max_events=1)
    limiter.reset()
    assert limiter.allow("ip", max_events=1)


def test_idle_keys_are_swept_out_of_memory() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(window_seconds=60.0, clock=clock)

    for i in range(100):
        limiter.allow(f"ip-{i}", max_events=5)

    clock.now = 120.0
    limiter.allow("fresh", max_events=5)
    assert len(limiter._events) == 1  # only "fresh" survives


class _DeadRedis:
    """Stands in for a Redis that cannot be reached."""

    async def incr(self, key: str) -> int:
        raise ConnectionError("no route to redis")

    async def expire(self, key: str, seconds: int) -> bool:  # pragma: no cover
        raise ConnectionError("no route to redis")


class _CountingRedis:
    """The smallest thing that behaves like INCR/EXPIRE."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expires: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.expires[key] = seconds
        return True


async def test_redis_limiter_enforces_the_budget_and_sets_a_ttl() -> None:
    redis = _CountingRedis()
    limiter = RedisFixedWindowRateLimiter(
        redis, window_seconds=3600, fallback=SlidingWindowRateLimiter(window_seconds=3600.0)
    )

    assert [await limiter.allow("k", 3) for _ in range(5)] == [True, True, True, False, False]
    # Expiry is set once, on the call that created the key — resetting it on
    # every hit would make a busy key immortal and the window meaningless.
    assert redis.expires == {"k": 3600}


async def test_an_unreachable_redis_degrades_and_never_fails_open() -> None:
    # The failure mode that matters: a Redis outage must not turn a security
    # control off. It falls back to the in-process limiter, which is weaker
    # (per worker) but still a limit.
    fallback = SlidingWindowRateLimiter(window_seconds=3600.0)
    limiter = RedisFixedWindowRateLimiter(_DeadRedis(), window_seconds=3600, fallback=fallback)

    assert [await limiter.allow("k", 2) for _ in range(4)] == [True, True, False, False]
