"""add Explore, person-to-person shares, friend links and the XP ledger

Four features that share a migration because they are one release and each is
small on its own.

**Explore** puts publishing columns on ``decks``. ``is_public`` is writable by
admins only for now: there is no report path and no moderation queue, and an
open publish button without one is an unreviewed-content problem rather than a
feature. The client's Explore tab reads the same endpoint either way, so
opening it later is a permission change and not a migration.

**Shares** are ``deck_shares``: one learner offering a deck to one person by
handle. Distinct from ``deck_invites``, and the difference is load-bearing —
an invite is a link anyone holding it can redeem, this waits for a named person
to say yes. Accepting makes them a member of the *same* deck; only Explore
takes a copy.

**Friend links** are a recency list, not a social graph, and are one-directional
by design: they reveal nothing the sharer did not already know, because they
typed the handle.

**XP** is the same hybrid the review history uses — an append-only ``xp_events``
ledger plus a ``users.xp`` counter every request reads. The partial unique index
on ``(user_id, action, day)`` is what makes the daily goal pay once: an
application check would let two sessions finishing together both collect.

The backfill computes each existing learner's XP from ``word_reviews`` at the
``grade_word`` rate. Session and daily-goal bonuses are not reconstructible and
are skipped. Starting everyone at zero was the alternative, and a profile that
resets to level 1 on the day XP became a server feature reads as data loss.

Revision ID: f4b8d2e61a97
Revises: e2c6a94f5b70
Create Date: 2026-08-06 19:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4b8d2e61a97"
down_revision: str | None = "e2c6a94f5b70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Must match XpAction.GRADE_WORD.points. Duplicated rather than imported: a
#: migration has to keep doing what it did on the day it ran, and the award
#: table is expected to change.
_GRADE_WORD_POINTS = 6


def upgrade() -> None:
    # ── Explore ──────────────────────────────────────────────────────────
    op.add_column(
        "decks",
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("decks", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "decks",
        sa.Column("category", sa.String(length=32), nullable=False, server_default="general"),
    )
    op.add_column("decks", sa.Column("description", sa.Text(), nullable=False, server_default=""))
    op.add_column(
        "decks", sa.Column("description_fa", sa.Text(), nullable=False, server_default="")
    )
    op.add_column(
        "decks",
        sa.Column("is_official", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "decks", sa.Column("save_count", sa.Integer(), nullable=False, server_default=sa.text("0"))
    )
    # Explore's driving access path: published decks, best first.
    op.create_index(
        "ix_decks_public_category",
        "decks",
        ["is_public", "category", "save_count"],
    )

    # ── person-to-person shares ──────────────────────────────────────────
    op.create_table(
        "deck_shares",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("deck_id", sa.Uuid(), nullable=False),
        sa.Column("from_user_id", sa.Uuid(), nullable=True),
        sa.Column("to_user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("shared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_deck_shares"),
        sa.ForeignKeyConstraint(
            ["deck_id"], ["decks.id"], name="fk_deck_shares_deck", ondelete="CASCADE"
        ),
        # SET NULL: the offer survives its sender deleting their account,
        # because what matters to the recipient is the deck.
        sa.ForeignKeyConstraint(
            ["from_user_id"], ["users.id"], name="fk_deck_shares_from", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["to_user_id"], ["users.id"], name="fk_deck_shares_to", ondelete="CASCADE"
        ),
        # Re-sharing refreshes the offer rather than stacking a second one.
        sa.UniqueConstraint("deck_id", "to_user_id", name="uq_deck_shares_deck_recipient"),
    )
    op.create_index("ix_deck_shares_recipient", "deck_shares", ["to_user_id", "shared_at"])

    # ── friends ──────────────────────────────────────────────────────────
    op.create_table(
        "friend_links",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("friend_user_id", sa.Uuid(), nullable=False),
        sa.Column("last_shared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("user_id", "friend_user_id", name="pk_friend_links"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_friend_links_user", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["friend_user_id"], ["users.id"], name="fk_friend_links_friend", ondelete="CASCADE"
        ),
    )

    # ── XP ───────────────────────────────────────────────────────────────
    op.add_column(
        "users", sa.Column("xp", sa.Integer(), nullable=False, server_default=sa.text("0"))
    )
    op.create_table(
        "xp_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("points", sa.SmallInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("ref_type", sa.String(length=16), nullable=True),
        sa.Column("ref_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_xp_events"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_xp_events_user", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_xp_events_user_occurred", "xp_events", ["user_id", "occurred_at"])
    # Partial, so only the daily goal is once-a-day; everything else repeats.
    op.create_index(
        "uq_xp_events_daily_goal",
        "xp_events",
        ["user_id", "action", "day"],
        unique=True,
        postgresql_where=sa.text("action = 'daily_goal'"),
        sqlite_where=sa.text("action = 'daily_goal'"),
    )

    _backfill_xp()


def _backfill_xp() -> None:
    """Credit existing learners for the reviews they have already done.

    One summarising ledger row per learner rather than one per review: the
    per-review events are already in ``word_reviews``, and duplicating a
    million of them here would buy nothing.
    """
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            INSERT INTO xp_events
                (id, user_id, action, points, occurred_at, day, ref_type, created_at)
            SELECT
                :uuid_expr,
                r.user_id,
                'backfill',
                LEAST(:points * count(*), 32767),
                now(),
                CURRENT_DATE,
                'migration',
                now()
            FROM word_reviews r
            GROUP BY r.user_id
            """.replace(
                ":uuid_expr",
                "gen_random_uuid()"
                if connection.dialect.name == "postgresql"
                else "lower(hex(randomblob(16)))",
            )
        ),
        {"points": _GRADE_WORD_POINTS},
    )
    # The counter is what every request reads, so it has to agree with the
    # ledger from the first moment rather than after the next award.
    connection.execute(
        sa.text(
            """
            UPDATE users SET xp = COALESCE((
                SELECT sum(e.points) FROM xp_events e WHERE e.user_id = users.id
            ), 0)
            """
        )
    )


def downgrade() -> None:
    op.drop_index("uq_xp_events_daily_goal", table_name="xp_events")
    op.drop_index("ix_xp_events_user_occurred", table_name="xp_events")
    op.drop_table("xp_events")
    op.drop_column("users", "xp")

    op.drop_table("friend_links")

    op.drop_index("ix_deck_shares_recipient", table_name="deck_shares")
    op.drop_table("deck_shares")

    op.drop_index("ix_decks_public_category", table_name="decks")
    for column in (
        "save_count",
        "is_official",
        "description_fa",
        "description",
        "category",
        "published_at",
        "is_public",
    ):
        op.drop_column("decks", column)
