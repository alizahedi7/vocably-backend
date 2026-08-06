"""The words/word_progress split, verified across the real migration chain.

The rest of the suite builds its schema with ``create_all``, so it only ever
sees the post-split shape and could not notice a backfill that lost a box or an
overview whose numbers moved. This module seeds a scratch database on the
*pre-split* schema, runs the real migrations over it, and asserts that
``/study/overview`` returns exactly what it returned before.

The trick is that "before" cannot be observed through the app — by the time the
tests run, the ORM is already post-split, and comparing the new overview against
a reimplementation of the old one only tests one query against another. So the
contract is pinned to a **literal**: a hand-written expected payload is checked
against an independent raw-SQL read of the old schema *before* migrating, and
against the real endpoint *after*. Neither side is derived from the other, and
the literal is what the Flutter client actually parses.

Postgres only; skipped on the default SQLite run.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core.database import get_session
from app.core.security import create_access_token
from app.main import app

_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = [
    pytest.mark.skipif(
        "postgresql" not in _TEST_DATABASE_URL,
        reason="the migration chain is Postgres-only; set TEST_DATABASE_URL to run",
    ),
    # The scratch database is migrated once for the module, so the tests sharing
    # it must also share the loop that fixture was set up on.
    pytest.mark.asyncio(loop_scope="module"),
]

_SCRATCH_DB = "vocably_word_split_test"
#: The last revision before the split — the schema the fixture is seeded on.
_PRE_SPLIT_REVISION = "e4b7d91a5c02"

ALI = UUID("11111111-1111-1111-1111-111111111111")
SARA = UUID("22222222-2222-2222-2222-222222222222")
#: Seeded with nothing at all. A user with no words is where a LEFT JOIN
#: aggregate most often returns NULL instead of 0, which is why they exist.
EMPTY = UUID("33333333-3333-3333-3333-333333333333")

DECK_A = UUID("aaaaaaaa-0000-0000-0000-00000000000a")
DECK_B = UUID("aaaaaaaa-0000-0000-0000-00000000000b")
WORD_IN_BOX_3 = UUID("cccccccc-0000-0000-0000-000000000002")

#: Ali's six words: three due in box 1, one not due in box 3, two in box 5 of
#: which one is due — across two decks, so due_deck_count is not trivially 1.
_ALI_WORDS = (
    (UUID("cccccccc-0000-0000-0000-000000000001"), DECK_A, 1, -1, 0),
    (UUID("cccccccc-0000-0000-0000-000000000004"), DECK_A, 1, -1, 0),
    (UUID("cccccccc-0000-0000-0000-000000000005"), DECK_A, 1, -1, 0),
    (WORD_IN_BOX_3, DECK_A, 3, +5, 7),
    (UUID("cccccccc-0000-0000-0000-000000000006"), DECK_B, 5, -1, 11),
    (UUID("cccccccc-0000-0000-0000-000000000007"), DECK_B, 5, +9, 4),
)

#: The payload the client parses, written out rather than computed. Every key is
#: hard-cast by lib/models/study_overview.dart — a missing one crashes the app.
EXPECTED_OVERVIEW_ALI: dict[str, Any] = {
    "due_count": 4,
    "total_count": 6,
    "learned_count": 2,
    "due_deck_count": 2,
    "estimated_minutes": 2,
    "streak": 3,
    "memory_strength": {
        "total": 6,
        "distribution": [
            {"box": 1, "label": "New", "count": 3},
            {"box": 2, "label": "Learning", "count": 0},
            {"box": 3, "label": "Familiar", "count": 1},
            {"box": 4, "label": "Known", "count": 0},
            {"box": 5, "label": "Mastered", "count": 2},
        ],
    },
}


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


def _alembic(scratch_url: str, revision: str) -> None:
    # A subprocess rather than alembic's Python API: env.py drives its own
    # asyncio.run, which cannot nest inside the running test loop, and it reads
    # the URL from settings at import time.
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        env={**os.environ, "DATABASE_URL": scratch_url, "ENV_FILE": os.devnull},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def seeded_pre_split() -> AsyncGenerator[AsyncEngine, None]:
    """A scratch database on the pre-split schema, with fixture rows."""
    base = _TEST_DATABASE_URL.rsplit("/", 1)[0]
    admin_url, scratch_url = f"{base}/postgres", f"{base}/{_SCRATCH_DB}"

    await _drop_scratch_db(admin_url)
    await _run_sql_outside_transaction(admin_url, f'CREATE DATABASE "{_SCRATCH_DB}"')
    _alembic(scratch_url, _PRE_SPLIT_REVISION)

    engine = create_async_engine(scratch_url)
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        for user_id, name, streak in ((ALI, "Ali", 3), (SARA, "Sara", 0), (EMPTY, "Nobody", 0)):
            await conn.execute(
                text(
                    "INSERT INTO users (id, auth_method, phone, name, native_language,"
                    " app_language, interests, daily_goal, streak, onboarded, is_admin,"
                    " created_at, updated_at) VALUES (:id, 'phone', :phone, :name, 'English',"
                    " 'English', '[]', 10, :streak, true, false, now(), now())"
                ),
                {
                    "id": user_id,
                    "phone": f"+9891200000{str(user_id)[:2]}",
                    "name": name,
                    "streak": streak,
                },
            )
        for deck_id, owner in ((DECK_A, ALI), (DECK_B, ALI)):
            await conn.execute(
                text(
                    "INSERT INTO decks (id, user_id, name, hue, created_at, updated_at)"
                    " VALUES (:id, :owner, 'D', 262, now(), now())"
                ),
                {"id": deck_id, "owner": owner},
            )
        for word_id, deck_id, box, due_offset_days, review_count in _ALI_WORDS:
            await conn.execute(
                text(
                    "INSERT INTO words (id, user_id, deck_id, term, meaning, box, due_at,"
                    " review_count, lapse_count, consecutive_correct, created_at, updated_at)"
                    " VALUES (:id, :user_id, :deck_id, :term, 'm', :box, :due_at, :rc, 0, 0,"
                    " now(), now())"
                ),
                {
                    "id": word_id,
                    "user_id": ALI,
                    "deck_id": deck_id,
                    "term": f"w{str(word_id)[-1]}",
                    "box": box,
                    "due_at": now + timedelta(days=due_offset_days),
                    "rc": review_count,
                },
            )

    yield engine
    await engine.dispose()
    await _drop_scratch_db(admin_url)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def migrated(seeded_pre_split: AsyncEngine) -> AsyncGenerator[AsyncEngine, None]:
    """The same database, brought up to head."""
    _alembic(str(seeded_pre_split.url.render_as_string(hide_password=False)), "head")
    yield seeded_pre_split


@pytest_asyncio.fixture(loop_scope="module")
async def migrated_client(migrated: AsyncEngine) -> AsyncGenerator[AsyncClient, None]:
    factory = async_sessionmaker(migrated, expire_on_commit=False)

    async def _session() -> AsyncGenerator[Any, None]:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = _session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


def _headers(user_id: UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


# ── before the migration ─────────────────────────────────────
async def test_the_fixture_produces_the_expected_overview_on_the_old_schema(
    seeded_pre_split: AsyncEngine,
) -> None:
    """Proves the fixture is what the literal above says it is.

    Read straight off the pre-split columns, independently of the endpoint, so
    the "after" assertion is comparing against something real rather than
    against itself.
    """
    async with seeded_pre_split.connect() as conn:
        # Guards the ordering this module depends on: this test must run before
        # the `migrated` fixture is first requested, or it is reading the new
        # schema and proving nothing. Failing here says so out loud.
        still_pre_split = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns"
                    " WHERE table_name = 'words' AND column_name = 'box'"
                )
            )
        ).scalar_one()
        assert still_pre_split == 1, "the split already ran; this test lost its ordering"

        rows = (
            await conn.execute(
                text(
                    "SELECT box, count(*), count(*) FILTER (WHERE due_at <= now()),"
                    " count(DISTINCT deck_id) FILTER (WHERE due_at <= now())"
                    " FROM words WHERE user_id = :u GROUP BY box"
                ),
                {"u": ALI},
            )
        ).all()
        streak = (
            await conn.execute(text("SELECT streak FROM users WHERE id = :u"), {"u": ALI})
        ).scalar_one()
        due_decks = (
            await conn.execute(
                text(
                    "SELECT count(DISTINCT deck_id) FROM words"
                    " WHERE user_id = :u AND due_at <= now()"
                ),
                {"u": ALI},
            )
        ).scalar_one()

    per_box = {int(box): int(count) for box, count, _, _ in rows}
    due_count = sum(int(due) for _, _, due, _ in rows)
    total = sum(per_box.values())

    assert due_count == EXPECTED_OVERVIEW_ALI["due_count"]
    assert total == EXPECTED_OVERVIEW_ALI["total_count"]
    assert int(due_decks) == EXPECTED_OVERVIEW_ALI["due_deck_count"]
    assert int(streak) == EXPECTED_OVERVIEW_ALI["streak"]
    assert per_box.get(4, 0) + per_box.get(5, 0) == EXPECTED_OVERVIEW_ALI["learned_count"]
    distribution = EXPECTED_OVERVIEW_ALI["memory_strength"]["distribution"]
    assert [per_box.get(b["box"], 0) for b in distribution] == [b["count"] for b in distribution]


# ── after the migration ──────────────────────────────────────
async def test_overview_is_byte_identical_after_the_split(migrated_client: AsyncClient) -> None:
    response = await migrated_client.get("/api/v1/study/overview", headers=_headers(ALI))
    assert response.status_code == 200, response.text
    assert response.json() == EXPECTED_OVERVIEW_ALI


async def test_a_user_with_no_words_reads_zeros_not_nulls(migrated_client: AsyncClient) -> None:
    response = await migrated_client.get("/api/v1/study/overview", headers=_headers(EMPTY))
    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 0
    assert body["due_count"] == 0
    assert body["due_deck_count"] == 0
    # Five bars, all present: the client hard-casts every one.
    assert [b["count"] for b in body["memory_strength"]["distribution"]] == [0, 0, 0, 0, 0]


async def test_word_payloads_still_carry_the_progress_keys(migrated_client: AsyncClient) -> None:
    listed = await migrated_client.get(
        "/api/v1/words", headers=_headers(ALI), params={"limit": 500}
    )
    assert listed.status_code == 200
    words = listed.json()
    assert len(words) == 6

    word = next(w for w in words if w["id"] == str(WORD_IN_BOX_3))
    # An Android build from before the split must not be able to tell.
    assert {"box", "due_at", "review_count", "last_reviewed_at"} <= word.keys()
    assert word["box"] == 3
    assert word["review_count"] == 7


async def test_every_card_yielded_exactly_one_progress_row(migrated: AsyncEngine) -> None:
    async with migrated.connect() as conn:
        words = (await conn.execute(text("SELECT count(*) FROM words"))).scalar_one()
        progress = (await conn.execute(text("SELECT count(*) FROM word_progress"))).scalar_one()
        owners = (
            await conn.execute(
                text("SELECT count(*) FROM word_progress WHERE user_id <> :u"), {"u": ALI}
            )
        ).scalar_one()
    # No fan-out, no loss: the backfill is one row per existing card, for the
    # user who owned it.
    assert words == 6
    assert progress == 6
    assert owners == 0


async def test_every_deck_got_an_owner_membership(migrated: AsyncEngine) -> None:
    async with migrated.connect() as conn:
        rows = (await conn.execute(text("SELECT deck_id, user_id, role FROM deck_members"))).all()
    # Without this backfill every existing deck becomes invisible to its owner
    # the moment reads start going through membership.
    assert {(deck_id, user_id, role) for deck_id, user_id, role in rows} == {
        (DECK_A, ALI, "owner"),
        (DECK_B, ALI, "owner"),
    }


async def test_the_schema_actually_moved(migrated: AsyncEngine) -> None:
    async with migrated.connect() as conn:
        columns = set(
            (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = 'words'"
                    )
                )
            )
            .scalars()
            .all()
        )
        fk = (
            await conn.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                    " WHERE conname = 'fk_words_created_by_user'"
                )
            )
        ).scalar_one()
        indexes = set(
            (
                await conn.execute(
                    text(
                        "SELECT indexname FROM pg_indexes"
                        " WHERE tablename IN ('words', 'word_progress')"
                    )
                )
            )
            .scalars()
            .all()
        )

    assert "created_by_user_id" in columns
    assert not {"user_id", "box", "due_at", "review_count", "last_grade"} & columns
    # SET NULL: a card outlives the account that wrote it, uncredited, so a
    # class keeps its vocabulary when a member leaves. RESTRICT was tried and
    # made every account undeletable — see revision e2c6a94f5b70.
    assert "ON DELETE SET NULL" in fk
    assert "ix_word_progress_user_due" in indexes
    assert "ix_words_user_due" not in indexes


async def test_existing_users_got_a_handle(migrated: AsyncEngine) -> None:
    async with migrated.connect() as conn:
        handles = dict(
            (await conn.execute(text("SELECT name, username FROM users ORDER BY name"))).all()
        )
    # Nobody is left unaddressable by sharing or the roster.
    assert all(handle for handle in handles.values())
    assert handles["Ali"] == "ali"
    assert handles["Sara"] == "sara"
