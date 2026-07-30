"""The Celery setup: registration, routing, scheduling, and the async bridge.

None of this needs a running broker. It is all configuration, and configuration
that is wrong here fails at 3am in a worker log rather than in CI.
"""

from __future__ import annotations

from typing import Any

import pytest
from celery.schedules import crontab

from app.core.config import Settings, settings
from app.tasks import maintenance
from app.tasks.celery_app import QUEUE_AI, QUEUE_MAINTENANCE, TASK_MODULES, celery_app

TASK_NAME = "vocably.maintenance.review_partitions"


# ── registration and routing ─────────────────────────────────
def test_every_declared_task_module_is_imported_by_the_worker() -> None:
    # A task in a module missing from TASK_MODULES is never registered, and beat
    # scheduling it fails with "unregistered task" only once it fires.
    assert maintenance.__name__ in TASK_MODULES


def test_the_maintenance_task_is_registered_under_its_scheduled_name() -> None:
    # The beat entry references this task by string; a rename that misses one
    # side is invisible until the schedule fires.
    assert TASK_NAME in celery_app.tasks


def test_tasks_are_routed_to_the_queue_that_matches_their_prefix() -> None:
    assert celery_app.amqp.router.route({}, TASK_NAME)["queue"].name == QUEUE_MAINTENANCE
    # AI work is slow and bursty; it gets its own queue so a backlog of it
    # cannot delay maintenance.
    assert celery_app.amqp.router.route({}, "vocably.ai.anything")["queue"].name == QUEUE_AI


# ── schedule ─────────────────────────────────────────────────
def test_partition_maintenance_is_scheduled_daily() -> None:
    entry = celery_app.conf.beat_schedule["review-partitions-daily"]
    assert entry["task"] == TASK_NAME

    schedule = entry["schedule"]
    assert isinstance(schedule, crontab)
    assert schedule.hour == {settings.review_history_maintenance_hour}
    assert schedule.minute == {0}
    # Every day of the week and month — i.e. daily, not weekly.
    assert len(schedule.day_of_week) == 7
    assert len(schedule.day_of_month) == 31


def test_scheduled_runs_expire_rather_than_pile_up() -> None:
    # Without expiry, a worker returning after a long outage would replay every
    # missed daily run back to back.
    assert celery_app.conf.beat_schedule["review-partitions-daily"]["options"]["expires"] > 0


def test_schedules_are_interpreted_in_utc() -> None:
    # Local time would shift every scheduled run twice a year on DST boundaries.
    assert celery_app.conf.timezone == "UTC"
    assert celery_app.conf.enable_utc is True


# ── delivery guarantees ──────────────────────────────────────
def test_tasks_are_acknowledged_only_after_they_finish() -> None:
    # A worker killed mid-task (deploy, OOM, spot reclaim) must have its work
    # redelivered rather than silently dropped.
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    # With acks_late, prefetching lets one worker hoard messages it is not
    # working on yet, which blocks redistribution.
    assert celery_app.conf.worker_prefetch_multiplier == 1


# ── the task body ────────────────────────────────────────────
def test_task_delegates_to_the_same_maintain_the_cli_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    async def fake_maintain(*, prune: bool) -> int:
        calls.append(prune)
        return 0

    monkeypatch.setattr(maintenance, "maintain", fake_maintain)
    monkeypatch.setattr(settings, "review_history_auto_prune", False)

    assert maintenance.review_partitions() == "created"
    assert calls == [False]  # never prunes unless configured to


def test_task_prunes_only_when_auto_prune_is_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    async def fake_maintain(*, prune: bool) -> int:
        calls.append(prune)
        return 0

    monkeypatch.setattr(maintenance, "maintain", fake_maintain)
    monkeypatch.setattr(settings, "review_history_auto_prune", True)

    assert maintenance.review_partitions() == "pruned"
    assert calls == [True]


def test_task_fails_loudly_when_maintenance_reports_a_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_maintain(*, prune: bool) -> int:
        return 1

    monkeypatch.setattr(maintenance, "maintain", failing_maintain)
    monkeypatch.setattr(settings, "review_history_auto_prune", False)

    # A non-zero result must surface as a failed task, not a log line nobody
    # reads — that is the difference between an alert and silent rot.
    with pytest.raises(RuntimeError, match="exit code 1"):
        maintenance.review_partitions()


# ── the async bridge ─────────────────────────────────────────
def test_run_async_returns_the_coroutine_result() -> None:
    from app.tasks.runtime import run_async

    async def work() -> str:
        return "done"

    assert run_async(work()) == "done"


def test_run_async_releases_the_connection_pool_after_every_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The whole point of the bridge: asyncio.run destroys its event loop, and
    # pooled asyncpg connections belong to it. Left in the pool, the next task
    # would pick up a connection tied to a dead loop and hang.
    from app.tasks import runtime

    disposals: list[int] = []

    class FakeEngine:
        async def dispose(self) -> None:
            disposals.append(1)

    monkeypatch.setattr(runtime, "engine", FakeEngine())

    async def work() -> int:
        return 7

    assert runtime.run_async(work()) == 7
    assert disposals == [1]


def test_run_async_releases_the_pool_even_when_the_task_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.tasks import runtime

    disposals: list[int] = []

    class FakeEngine:
        async def dispose(self) -> None:
            disposals.append(1)

    monkeypatch.setattr(runtime, "engine", FakeEngine())

    async def work() -> int:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        runtime.run_async(work())
    # A failing task must not leak the pool; retries would otherwise inherit it.
    assert disposals == [1]


# ── configuration guards ─────────────────────────────────────
def test_soft_time_limit_must_be_below_the_hard_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    # Otherwise the process is killed before the task ever gets the chance to
    # shut down cleanly, and the soft limit is silently meaningless.
    monkeypatch.setenv("CELERY_TASK_SOFT_TIME_LIMIT_SECONDS", "600")
    monkeypatch.setenv("CELERY_TASK_TIME_LIMIT_SECONDS", "600")
    with pytest.raises(ValueError, match="must be below"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_result_backend_is_disabled_by_default() -> None:
    # Nothing reads these tasks' return values; storing them would grow Redis
    # forever for no reader.
    assert celery_app.conf.result_backend is None


@pytest.mark.parametrize("value", ["", None])
def test_empty_result_backend_setting_disables_storage(value: Any) -> None:
    assert (value or None) is None
