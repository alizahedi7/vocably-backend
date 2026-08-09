"""Releasing loop-bound pools between Celery tasks.

The bug this pins was found in production, not in a test: a deck build's first
batch ran on the dictionary-grounded path and every later batch silently did not.
The cause is that ``asyncio.run`` gives each task a fresh event loop while the
Redis and HTTP clients are memoized per process — so the second task inherits a
pool bound to a dead loop.

The database pool fails loudly when that happens. **These do not.** Every call
through them is deliberately best-effort, so the failure is caught, logged at
INFO, and degrades to "no cache" or "no dictionary" — which reads as a slow,
oddly ungrounded build rather than as an error. That silence is the whole reason
this needs a test.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any

import pytest

from app.tasks import runtime


class FakePool:
    """Stands in for a client whose connections belong to one event loop."""

    def __init__(self) -> None:
        self.loop = asyncio.get_event_loop()
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True

    def use(self) -> None:
        """Raise the way a real pool does when reached from another loop."""
        if asyncio.get_event_loop() is not self.loop:
            raise RuntimeError("attached to a different loop")


@pytest.fixture
def fake_factory(monkeypatch: pytest.MonkeyPatch) -> Any:
    built: list[FakePool] = []

    @lru_cache
    def factory() -> FakePool:
        pool = FakePool()
        built.append(pool)
        return pool

    factory.built = built  # type: ignore[attr-defined]
    monkeypatch.setattr(runtime, "_POOLED_FACTORIES", (factory,))

    # The engine is a real AsyncEngine whose `dispose` is read-only, and these
    # tests are about the *other* pools — patch the call site, not the object.
    async def _no_engine_dispose() -> None:
        return None

    class _Engine:
        dispose = staticmethod(_no_engine_dispose)

    monkeypatch.setattr(runtime, "engine", _Engine)
    return factory


def test_a_second_task_gets_a_pool_on_its_own_loop(fake_factory: Any) -> None:
    """The regression itself: without release, the second run raises."""

    async def work() -> None:
        fake_factory().use()

    runtime.run_async(work())
    runtime.run_async(work())

    assert len(fake_factory.built) == 2, "each task must build its own pool"
    assert fake_factory.built[0].closed is True
    assert fake_factory.built[0].loop is not fake_factory.built[1].loop


def test_a_pool_never_touched_is_not_built_just_to_close_it(fake_factory: Any) -> None:
    """Releasing must not instantiate a client the task never needed."""

    async def work() -> str:
        return "did not touch the dictionary"

    assert runtime.run_async(work()) == "did not touch the dictionary"
    assert fake_factory.built == []


def test_a_failing_close_neither_fails_the_task_nor_keeps_the_stale_pool(
    fake_factory: Any,
) -> None:
    """Teardown is best-effort, but the cache must still be cleared.

    Leaving the dead pool cached would push the failure onto the *next* task,
    which is precisely the bug — so the clear happens even when the close does
    not.
    """

    async def work() -> str:
        pool = fake_factory()

        async def explode() -> None:
            raise OSError("connection already gone")

        pool.aclose = explode  # type: ignore[method-assign]
        return "work still finished"

    assert runtime.run_async(work()) == "work still finished"

    async def use_again() -> None:
        fake_factory().use()

    runtime.run_async(use_again())
    assert len(fake_factory.built) == 2


def test_the_real_factories_are_registered() -> None:
    """A new process-wide async client that is not registered fails silently."""
    from app.infrastructure.ai.factory import single_flight
    from app.infrastructure.dictionary.factory import dictionary_service

    assert dictionary_service in runtime._POOLED_FACTORIES
    assert single_flight in runtime._POOLED_FACTORIES
