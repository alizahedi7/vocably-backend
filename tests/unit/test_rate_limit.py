"""Sliding-window rate limiter used for OTP abuse protection."""

from __future__ import annotations

from app.core.rate_limit import SlidingWindowRateLimiter


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
