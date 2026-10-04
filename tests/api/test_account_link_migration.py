"""The account-linking migration, run over rows shaped like production's.

The rest of the suite builds its schema with ``create_all`` and so never runs
the one part of this change that touches data it cannot see: lowercasing every
stored email, and deciding which of them count as verified. A backfill that
marked two rows with one address would fail the unique index and take the
deploy with it; one that marked too much would bless addresses nobody checked.

Postgres only; skipped on the default SQLite run.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncGenerator
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = [
    pytest.mark.skipif(
        "postgresql" not in _TEST_DATABASE_URL,
        reason="the migration chain is Postgres-only; set TEST_DATABASE_URL to run",
    ),
    pytest.mark.asyncio(loop_scope="module"),
]

_SCRATCH_DB = "vocably_account_link_test"
#: The last revision before linking — the schema the fixture is seeded on.
_BEFORE = "d2f5c8a1e764"
_AFTER = "e5b7a2c94d18"

GMAIL = UUID("11111111-0000-0000-0000-000000000001")
MIXED_CASE_GMAIL = UUID("11111111-0000-0000-0000-000000000002")
WORKSPACE = UUID("11111111-0000-0000-0000-000000000003")
TWIN_A = UUID("11111111-0000-0000-0000-000000000004")
TWIN_B = UUID("11111111-0000-0000-0000-000000000005")
PHONE_ONLY = UUID("11111111-0000-0000-0000-000000000006")
NO_EMAIL = UUID("11111111-0000-0000-0000-000000000007")

#: (id, phone, google_sub, email as stored before the migration)
_USERS = (
    (GMAIL, None, "g-1", "ali@gmail.com"),
    (MIXED_CASE_GMAIL, None, "g-2", "  Sara.K@Gmail.com "),
    # Google may well be authoritative here, but nothing recorded whether it
    # said so — it is proven at the next sign-in, not assumed now.
    (WORKSPACE, None, "g-3", "reza@corp.example"),
    # Two rows, one address once lowercased. Marking both would fail the index.
    (TWIN_A, None, "g-4", "twin@gmail.com"),
    (TWIN_B, None, "g-5", "Twin@gmail.com"),
    (PHONE_ONLY, "+989120000006", None, None),
    (NO_EMAIL, None, "g-7", None),
)


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
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{_SCRATCH_DB}' AND pid <> pg_backend_pid()",
        f'DROP DATABASE IF EXISTS "{_SCRATCH_DB}"',
    )


def _alembic(scratch_url: str, *args: str) -> None:
    # A subprocess rather than alembic's Python API: env.py drives its own
    # asyncio.run, which cannot nest inside the running test loop.
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env={**os.environ, "DATABASE_URL": scratch_url, "ENV_FILE": os.devnull},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def migrated() -> AsyncGenerator[AsyncEngine, None]:
    """A scratch database seeded before the migration and upgraded across it."""
    base = _TEST_DATABASE_URL.rsplit("/", 1)[0]
    admin_url, scratch_url = f"{base}/postgres", f"{base}/{_SCRATCH_DB}"

    await _drop_scratch_db(admin_url)
    await _run_sql_outside_transaction(admin_url, f'CREATE DATABASE "{_SCRATCH_DB}"')
    _alembic(scratch_url, "upgrade", _BEFORE)

    engine = create_async_engine(scratch_url)
    async with engine.begin() as conn:
        for user_id, phone, google_sub, email in _USERS:
            await conn.execute(
                text(
                    "INSERT INTO users (id, auth_method, phone, google_sub, email, name,"
                    " native_language, app_language, interests, daily_goal, streak, xp,"
                    " onboarded, is_admin, created_at, updated_at)"
                    " VALUES (:id, :method, :phone, :sub, :email, 'Someone', 'English',"
                    " 'English', '[]', 10, 0, 0, true, false, now(), now())"
                ),
                {
                    "id": user_id,
                    "method": "phone" if phone else "google",
                    "phone": phone,
                    "sub": google_sub,
                    "email": email,
                },
            )

    _alembic(scratch_url, "upgrade", _AFTER)
    yield engine
    await engine.dispose()
    await _drop_scratch_db(admin_url)


async def _row(engine: AsyncEngine, user_id: UUID) -> tuple[str | None, bool]:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT email, is_email_verified FROM users WHERE id = :id"),
                {"id": user_id},
            )
        ).one()
    return row.email, row.is_email_verified


async def test_gmail_addresses_on_google_accounts_are_marked_verified(
    migrated: AsyncEngine,
) -> None:
    assert await _row(migrated, GMAIL) == ("ali@gmail.com", True)
    # Lowercased and trimmed first, so the index sees one spelling.
    assert await _row(migrated, MIXED_CASE_GMAIL) == ("sara.k@gmail.com", True)


async def test_nothing_else_is_assumed_verified(migrated: AsyncEngine) -> None:
    assert await _row(migrated, WORKSPACE) == ("reza@corp.example", False)
    assert await _row(migrated, PHONE_ONLY) == (None, False)
    assert await _row(migrated, NO_EMAIL) == (None, False)


async def test_two_rows_sharing_an_address_are_both_left_unverified(
    migrated: AsyncEngine,
) -> None:
    # The migration completed at all, which is the point: verifying either
    # twin would have made the unique index impossible to build.
    assert await _row(migrated, TWIN_A) == ("twin@gmail.com", False)
    assert await _row(migrated, TWIN_B) == ("twin@gmail.com", False)


async def test_a_proven_email_is_unique_and_a_claimed_one_is_not(
    migrated: AsyncEngine,
) -> None:
    async with migrated.begin() as conn:
        # Claiming an address somebody verified is allowed: it is only a string.
        await conn.execute(
            text("UPDATE users SET email = 'ali@gmail.com' WHERE id = :id"), {"id": NO_EMAIL}
        )
    with pytest.raises(IntegrityError, match="uq_users_verified_email"):
        async with migrated.begin() as conn:
            await conn.execute(
                text("UPDATE users SET is_email_verified = true WHERE id = :id"),
                {"id": NO_EMAIL},
            )


async def test_the_verified_flag_cannot_outlive_its_address(migrated: AsyncEngine) -> None:
    with pytest.raises(IntegrityError, match="ck_users_verified_email_present"):
        async with migrated.begin() as conn:
            await conn.execute(text("UPDATE users SET email = NULL WHERE id = :id"), {"id": GMAIL})


async def test_a_challenge_goes_when_its_account_does(migrated: AsyncEngine) -> None:
    async with migrated.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO account_link_challenges"
                " (user_id, kind, target, code_hash, attempts_left, expires_at)"
                " VALUES (:id, 'phone', '+989120000099', 'h', 3, now())"
            ),
            {"id": WORKSPACE},
        )
        await conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": WORKSPACE})
        left = await conn.scalar(text("SELECT count(*) FROM account_link_challenges"))
    assert left == 0


async def test_the_migration_reverses_cleanly(migrated: AsyncEngine) -> None:
    """Last in the module on purpose: it takes the schema back down."""
    await migrated.dispose()
    scratch_url = f"{_TEST_DATABASE_URL.rsplit('/', 1)[0]}/{_SCRATCH_DB}"

    _alembic(scratch_url, "downgrade", _BEFORE)

    async with migrated.connect() as conn:
        columns = (
            await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'users'"
                )
            )
        ).scalars()
        assert "is_email_verified" not in set(columns)
        assert await conn.scalar(text("SELECT to_regclass('account_link_challenges')")) is None
