"""add deck_invites and daily_deck_activity

Two tables that complete shared decks.

``deck_invites`` is the link a teacher hands to a class. The deck is the
primary key — one link per deck — so re-opening a closed link hands back the
same code; a code that changed on every open would invalidate one already given
out. The code is a **bearer credential**, so it is unique-indexed and generated
with a CSPRNG at ~65 bits; the client's local stand-in derives a 6-character
code from ``deckId.hashCode`` and is a UI placeholder that must never be copied.

``daily_deck_activity`` is a rollup, not a log. The roster shows every member's
activity this week, and computing that from ``word_reviews`` would break the
rule in CLAUDE.md that no user-facing request aggregates over the event log — a
roster of thirty students would scan it thirty times. Counters are incremented
on the same transaction the grade already opens.

The backfill below reconstructs it from ``word_reviews``, which is exactly what
that log was kept for: existing learners arrive on the roster with real history
instead of zeros. ``day`` is derived from ``reviewed_at`` in the learner's own
timezone where they have set one, falling back to UTC — the same rule
``app/domain/services/calendar.py`` applies going forward. ``mastered`` counts
transitions *into* box 5 (``box_after = 5 AND box_before <> 5``), so a learner
who lapsed out and returned is not counted twice.

Revision ID: d1a5b7c34e86
Revises: c9e4f6a03d25
Create Date: 2026-08-06 15:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1a5b7c34e86"
down_revision: str | None = "c9e4f6a03d25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deck_invites",
        sa.Column("deck_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("is_open", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("deck_id", name="pk_deck_invites"),
        sa.ForeignKeyConstraint(
            ["deck_id"], ["decks.id"], name="fk_deck_invites_deck", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_deck_invites_created_by",
            ondelete="SET NULL",
        ),
    )
    # Unique because two decks sharing a code is an access-control failure,
    # not a collision to resolve.
    op.create_index("ix_deck_invites_code", "deck_invites", ["code"], unique=True)

    op.create_table(
        "daily_deck_activity",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("deck_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("reviews", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("mastered", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("user_id", "deck_id", "day", name="pk_daily_deck_activity"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_daily_deck_activity_user", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["deck_id"], ["decks.id"], name="fk_daily_deck_activity_deck", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_daily_deck_activity_deck_day", "daily_deck_activity", ["deck_id", "day"])

    _backfill_activity()


def _backfill_activity() -> None:
    """Rebuild the rollup from the review log.

    Postgres only: it needs ``AT TIME ZONE`` to bucket by the learner's local
    day. Nothing runs this migration on SQLite (the test suite builds its schema
    with ``create_all``), and a SQLite run simply starts the rollup empty — the
    counters are additive from the next grade either way.
    """
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        sa.text(
            """
            INSERT INTO daily_deck_activity (user_id, deck_id, day, reviews, mastered)
            SELECT r.user_id,
                   r.deck_id,
                   (r.reviewed_at AT TIME ZONE COALESCE(u.timezone, 'UTC'))::date AS day,
                   count(*),
                   count(*) FILTER (WHERE r.box_after = 5 AND r.box_before <> 5)
            FROM word_reviews r
            JOIN users u ON u.id = r.user_id
            -- The FKs are ON DELETE CASCADE, but word_reviews outlives a deck
            -- delete only if one ever raced; the join keeps the insert honest.
            JOIN decks d ON d.id = r.deck_id
            GROUP BY r.user_id, r.deck_id, day
            ON CONFLICT (user_id, deck_id, day) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_daily_deck_activity_deck_day", table_name="daily_deck_activity")
    op.drop_table("daily_deck_activity")
    op.drop_index("ix_deck_invites_code", table_name="deck_invites")
    op.drop_table("deck_invites")
