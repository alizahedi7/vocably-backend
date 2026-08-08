"""add user handles, learning profile fields, and a timezone

Sharing, friends and the roster all address people by a handle, so ``username``
becomes the one user-chosen string other people type. It is stored
already-lowercased behind a unique index rather than normalised in the
application, so two casings of the same handle cannot both exist.

Existing accounts are onboarded and will never see the setup step that asks for
one, so leaving them NULL forever would mean sharing and the roster cannot
address them. The backfill below derives a handle the same way the client's
``AppState.slugifyUsername`` does — lowercase, drop anything outside
``[a-z0-9_]``, must start with a letter, 3–20 characters — then appends the
shortest free numeric suffix on a collision, and falls back to
``user_<first 8 of id>``. It is deterministic in the row, so a re-run proposes
the same handle rather than a second one.

``timezone`` exists because every "today" in this product is a local question:
a streak, a daily goal and the roster's weekly figures all turn on where a day
starts, and a learner in Tehran was getting a UTC boundary at 03:30 local. NULL
means UTC. See ``app/domain/services/calendar.py`` — nothing else computes a day.

``proficiency`` and ``study_time`` are free-form keys, deliberately not enums:
the lists behind them are product surfaces that change faster than a migration.

Revision ID: b8d3e5f92c14
Revises: a7f2c8b41d63
Create Date: 2026-08-06 11:30:00.000000
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8d3e5f92c14"
down_revision: str | None = "a7f2c8b41d63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Kept in step with app/domain/services/usernames.py. Duplicated rather than
#: imported on purpose: a migration must keep doing what it did on the day it
#: ran, and importing application code makes it change under a later refactor.
_RESERVED = {
    "admin",
    "administrator",
    "api",
    "help",
    "join",
    "me",
    "null",
    "root",
    "settings",
    "support",
    "system",
    "undefined",
    "user",
    "vocably",
}
_MAX_LENGTH = 20
_MIN_LENGTH = 3


def _slugify(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9_]", "", (raw or "").strip().lower())
    slug = re.sub(r"^[^a-z]+", "", slug)[:_MAX_LENGTH]
    return slug if len(slug) >= _MIN_LENGTH else ""


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=20), nullable=True))
    op.add_column("users", sa.Column("target_language", sa.String(length=64), nullable=True))
    op.add_column("users", sa.Column("proficiency", sa.String(length=32), nullable=True))
    op.add_column("users", sa.Column("study_time", sa.String(length=32), nullable=True))
    op.add_column("users", sa.Column("timezone", sa.String(length=64), nullable=True))

    _backfill_usernames()

    # Created after the backfill: a unique index over a column being filled row
    # by row is pure overhead, and the collision check below reads the taken set
    # from memory anyway.
    op.create_index("ix_users_username", "users", ["username"], unique=True)


def _backfill_usernames() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, name FROM users ORDER BY created_at, id")
    ).fetchall()

    taken: set[str] = set()
    strategies = {"slug": 0, "suffixed": 0, "fallback": 0}

    for user_id, name in rows:
        candidate = _slugify(name or "")
        strategy = "slug"

        if not candidate or candidate in _RESERVED or candidate in taken:
            base = candidate if candidate and candidate not in _RESERVED else ""
            if base:
                # Shortest free numeric suffix, truncating the base so the whole
                # handle stays inside the column.
                suffix = 2
                while True:
                    trimmed = base[: _MAX_LENGTH - len(str(suffix))]
                    attempt = f"{trimmed}{suffix}"
                    if attempt not in taken and attempt not in _RESERVED:
                        candidate, strategy = attempt, "suffixed"
                        break
                    suffix += 1
            else:
                candidate = f"user_{str(user_id).replace('-', '')[:8]}"
                strategy = "fallback"

        taken.add(candidate)
        strategies[strategy] += 1
        connection.execute(
            sa.text("UPDATE users SET username = :username WHERE id = :id"),
            {"username": candidate, "id": user_id},
        )

    # Logged rather than silent: which strategy dominated is the difference
    # between "handles look like people's names" and "everyone got user_1a2b3c4d".
    print(  # noqa: T201 — alembic reports progress on stdout
        f"backfilled {len(rows)} usernames: "
        f"{strategies['slug']} from name, "
        f"{strategies['suffixed']} suffixed, "
        f"{strategies['fallback']} fallback"
    )


def downgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "timezone")
    op.drop_column("users", "study_time")
    op.drop_column("users", "proficiency")
    op.drop_column("users", "target_language")
    op.drop_column("users", "username")
