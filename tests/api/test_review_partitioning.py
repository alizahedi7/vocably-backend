"""The partitioning contract of ``word_reviews``, verified through real migrations.

The rest of the suite builds its schema with ``Base.metadata.create_all``, which
produces an ordinary unpartitioned table — the ORM model cannot express
``PARTITION BY``, and deliberately does not try (see the model's docstring). So
nothing else in the suite would notice if the migration stopped partitioning,
started routing rows to the wrong month, or dropped the safety-net default
partition. This module applies the actual migration chain to a scratch database
and asserts those properties directly.

Postgres only; skipped when ``TEST_DATABASE_URL`` is unset, as on the default
SQLite run.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = [
    pytest.mark.skipif(
        "postgresql" not in _TEST_DATABASE_URL,
        reason="partitioning is a Postgres feature; set TEST_DATABASE_URL to run",
    ),
    # The scratch database is migrated once for the module, so the tests sharing
    # it must also share the loop that fixture was set up on.
    pytest.mark.asyncio(loop_scope="module"),
]

_SCRATCH_DB = "vocably_partition_test"


async def _run_sql_outside_transaction(url: str, *statements: str) -> None:
    # CREATE/DROP DATABASE cannot run inside a transaction block.
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        for statement in statements:
            await conn.execute(text(statement))
    await engine.dispose()


async def _drop_scratch_db(admin_url: str) -> None:
    await _run_sql_outside_transaction(
        admin_url,
        # A connection left over from an interrupted run would otherwise make
        # DROP DATABASE fail and wedge every later run of this module.
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{_SCRATCH_DB}' AND pid <> pg_backend_pid()",
        f'DROP DATABASE IF EXISTS "{_SCRATCH_DB}"',
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def migrated_engine() -> AsyncGenerator[AsyncEngine, None]:
    admin_url = _TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
    scratch_url = _TEST_DATABASE_URL.rsplit("/", 1)[0] + "/" + _SCRATCH_DB

    await _drop_scratch_db(admin_url)
    await _run_sql_outside_transaction(admin_url, f'CREATE DATABASE "{_SCRATCH_DB}"')

    # A subprocess rather than alembic's Python API: env.py drives its own
    # asyncio.run, which cannot nest inside the running test loop, and it reads
    # the URL from settings at import time.
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={**os.environ, "DATABASE_URL": scratch_url, "ENV_FILE": os.devnull},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    engine = create_async_engine(scratch_url)
    yield engine
    await engine.dispose()
    await _drop_scratch_db(admin_url)


async def test_word_reviews_is_range_partitioned_by_month(migrated_engine: AsyncEngine) -> None:
    async with migrated_engine.connect() as conn:
        # The table name is a literal, not a bind param: ":t::regclass" would be
        # parsed as the parameter ":t:" followed by "regclass".
        strategy = (
            await conn.execute(
                # ::text because partstrat is a "char", which the driver hands
                # back as bytes.
                text(
                    "SELECT partstrat::text FROM pg_partitioned_table"
                    " WHERE partrelid = 'word_reviews'::regclass"
                )
            )
        ).scalar_one_or_none()
        key = (
            await conn.execute(text("SELECT pg_get_partkeydef('word_reviews'::regclass)"))
        ).scalar_one()

    assert strategy == "r", "word_reviews must be RANGE partitioned"
    assert key == "RANGE (reviewed_at)"


async def test_a_window_of_partitions_and_a_default_exist(migrated_engine: AsyncEngine) -> None:
    async with migrated_engine.connect() as conn:
        names = set(
            (
                await conn.execute(
                    text(
                        "SELECT c.relname FROM pg_class c JOIN pg_inherits i "
                        "ON i.inhrelid = c.oid WHERE i.inhparent = 'word_reviews'::regclass"
                    )
                )
            )
            .scalars()
            .all()
        )

    monthly = {n for n in names if n != "word_reviews_default"}
    # The safety net: an insert must never fail merely because maintenance lapsed.
    assert "word_reviews_default" in names
    assert len(monthly) >= 12, f"expected a year of pre-created months, got {sorted(monthly)}"
    assert f"word_reviews_{datetime.now(UTC):%Y%m}" in monthly


async def test_the_composite_primary_key_carries_the_partition_key(
    migrated_engine: AsyncEngine,
) -> None:
    # Postgres requires the partition key in every unique constraint; if this
    # regressed the table simply could not be created as partitioned.
    async with migrated_engine.connect() as conn:
        definition = (
            await conn.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = 'pk_word_reviews'"
                )
            )
        ).scalar_one()
    assert definition == "PRIMARY KEY (id, reviewed_at)"


async def test_rows_route_to_the_partition_for_their_month(migrated_engine: AsyncEngine) -> None:
    user_id, deck_id, word_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    next_month = (now.replace(day=1) + timedelta(days=40)).replace(day=1)

    async with migrated_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, auth_method, phone, name, native_language, app_language,"
                " onboarded, streak, daily_goal, is_admin, created_at, updated_at) VALUES"
                " (:id, 'phone', :phone, 'T', 'English', 'English', true, 0, 10, false,"
                " now(), now())"
            ),
            {"id": user_id, "phone": f"+9891{uuid4().int % 10**8:08d}"},
        )
        await conn.execute(
            text(
                "INSERT INTO decks (id, user_id, name, hue, created_at, updated_at)"
                " VALUES (:id, :user_id, 'D', 1, now(), now())"
            ),
            {"id": deck_id, "user_id": user_id},
        )
        await conn.execute(
            text(
                "INSERT INTO words (id, user_id, deck_id, term, meaning, box, due_at,"
                " review_count, lapse_count, consecutive_correct, created_at, updated_at)"
                " VALUES (:id, :user_id, :deck_id, 'x', 'y', 1, now(), 0, 0, 0, now(), now())"
            ),
            {"id": word_id, "user_id": user_id, "deck_id": deck_id},
        )
        for reviewed_at in (now, next_month):
            await conn.execute(
                text(
                    "INSERT INTO word_reviews (user_id, word_id, deck_id, reviewed_at,"
                    " grade, box_before, box_after) VALUES (:u, :w, :d, :at, 2, 1, 2)"
                ),
                {"u": user_id, "w": word_id, "d": deck_id, "at": reviewed_at},
            )

        placements = dict(
            (
                await conn.execute(
                    text(
                        "SELECT to_char(reviewed_at, 'YYYYMM'), tableoid::regclass::text"
                        " FROM word_reviews WHERE word_id = :w"
                    ),
                    {"w": word_id},
                )
            ).all()
        )

    assert placements == {
        f"{now:%Y%m}": f"word_reviews_{now:%Y%m}",
        f"{next_month:%Y%m}": f"word_reviews_{next_month:%Y%m}",
    }
    # Nothing should ever land in the default while the window is maintained.
    assert "word_reviews_default" not in placements.values()


async def test_check_constraints_reject_undecodable_enum_encodings(
    migrated_engine: AsyncEngine,
) -> None:
    # A row that cannot be decoded back into a ReviewGrade is worse than a
    # rejected insert: it is only discovered at read time, long afterwards.
    async with migrated_engine.connect() as conn:
        constraints = set(
            (
                await conn.execute(
                    text(
                        "SELECT conname FROM pg_constraint"
                        " WHERE conrelid = 'word_reviews'::regclass AND contype = 'c'"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert {
        "ck_word_reviews_grade",
        "ck_word_reviews_box_before",
        "ck_word_reviews_box_after",
    } <= constraints
