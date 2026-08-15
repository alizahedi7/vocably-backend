"""split per-user study state off the shared card

``words`` carried one learner's boxes as columns on the card itself, so a deck
could only ever be one person's: sharing it would either share one set of boxes
between thirty students or fork the words into thirty copies. This revision
moves that state to ``word_progress``, keyed ``(user_id, word_id)``, and demotes
``words.user_id`` to ``created_by_user_id`` — attribution, never an
authorization check again.

``deck_members`` lands here too, rather than with the sharing endpoints, because
the split *defines* a learner's word set as "words in every deck I belong to":
from the moment progress stops living on the card, the due queue and every
aggregate join through it. Only the table and its owner backfill are here; the
invite/join/roster endpoints come later.

Progress rows are created lazily from here on. Only ``grade`` writes one, and a
missing row reads as box 1 / due now, so sharing a 500-word deck with a class of
thirty writes nothing. The backfill below is the one and only fan-out that will
ever happen, and it is exact — one row per existing card, for the user who owned
it, so no learner loses a box.

The column drop happens in this same revision, deliberately: a window where both
copies exist and both are written is how they silently diverge. ``words`` is a
few thousand rows per user in a product this size, so one transaction is right.
Above roughly a million rows, commit at the marked seam and stage the backfill.

``downgrade()`` folds each user's progress back onto their own cards and
**loses progress rows for words the user did not create** — under shared decks
there is nowhere on a single-owner card to put a second member's boxes. It is a
schema rollback, not a data rollback.

SQLite gets the tables, the rename and the column drops but not the foreign-key
swap: SQLite cannot alter a constraint without rebuilding the table, and nothing
runs this migration on SQLite — the test suite builds its schema with
``Base.metadata.create_all`` (see ``tests/api/conftest.py``) and the only
migration tests are Postgres-only. Same precedent as e4b7d91a5c02's partitioning.

Revision ID: a7f2c8b41d63
Revises: e4b7d91a5c02
Create Date: 2026-08-06 09:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7f2c8b41d63"
down_revision: str | None = "e4b7d91a5c02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The per-learner columns leaving ``words``.
_PROGRESS_COLUMNS = (
    "box",
    "due_at",
    "review_count",
    "last_reviewed_at",
    "lapse_count",
    "consecutive_correct",
    "first_reviewed_at",
    "mastered_at",
    "last_grade",
)


def upgrade() -> None:
    is_postgres = op.get_bind().dialect.name == "postgresql"

    # ── 1. deck_members: the new authorization source ────────────────────
    op.create_table(
        "deck_members",
        sa.Column("deck_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("invited_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("deck_id", "user_id", name="pk_deck_members"),
        sa.ForeignKeyConstraint(
            ["deck_id"], ["decks.id"], name="fk_deck_members_deck", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_deck_members_user", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            name="fk_deck_members_invited_by",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_deck_members_user", "deck_members", ["user_id"])

    # Every existing deck gets its creator as owner. Without this, every deck in
    # the database becomes invisible to everyone the moment reads start going
    # through membership.
    op.execute(
        sa.text(
            """
            INSERT INTO deck_members
                (deck_id, user_id, role, joined_at, created_at, updated_at)
            SELECT d.id, d.user_id, 'owner', d.created_at, d.created_at, d.updated_at
            FROM decks d
            """
        )
    )

    # ── 2. word_progress ─────────────────────────────────────────────────
    op.create_table(
        "word_progress",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("word_id", sa.Uuid(), nullable=False),
        sa.Column("deck_id", sa.Uuid(), nullable=False),
        sa.Column("box", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lapse_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("consecutive_correct", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("first_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mastered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_grade", sa.SmallInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("user_id", "word_id", name="pk_word_progress"),
        # CASCADE from users: erasing an account erases that person's boxes and
        # nothing else. A member leaving must never delete a class's cards,
        # which is exactly why this cascade lands here and not on `words`.
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_word_progress_user", ondelete="CASCADE"
        ),
        # CASCADE from words: deleting a card takes everyone's progress with it.
        sa.ForeignKeyConstraint(
            ["word_id"], ["words.id"], name="fk_word_progress_word", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["deck_id"], ["decks.id"], name="fk_word_progress_deck", ondelete="CASCADE"
        ),
        sa.CheckConstraint("box BETWEEN 1 AND 5", name="ck_word_progress_box"),
    )
    op.create_index("ix_word_progress_user_due", "word_progress", ["user_id", "due_at"])
    op.create_index(
        "ix_word_progress_user_deck_due", "word_progress", ["user_id", "deck_id", "due_at"]
    )
    op.create_index("ix_word_progress_deck_user", "word_progress", ["deck_id", "user_id"])

    # ── 3. backfill: one row per existing card, exactly ──────────────────
    # updated_at carries words.updated_at rather than now(): the progress row
    # *is* the thing that was last touched, and preserving it keeps every
    # "last changed" reading identical across the migration.
    op.execute(
        sa.text(
            """
            INSERT INTO word_progress (
                user_id, word_id, deck_id, box, due_at, review_count,
                last_reviewed_at, lapse_count, consecutive_correct,
                first_reviewed_at, mastered_at, last_grade, created_at, updated_at)
            SELECT w.user_id, w.id, w.deck_id, w.box, w.due_at, w.review_count,
                   w.last_reviewed_at, w.lapse_count, w.consecutive_correct,
                   w.first_reviewed_at, w.mastered_at, w.last_grade,
                   w.created_at, w.updated_at
            FROM words w
            """
        )
    )
    # ── seam: if `words` ever exceeds ~1M rows, commit here ──────────────

    # ── 4. words: demote user_id, move the indexes, drop the state ───────
    op.drop_index("ix_words_user_due", table_name="words")
    op.drop_index(op.f("ix_words_due_at"), table_name="words")
    op.drop_index(op.f("ix_words_user_id"), table_name="words")

    # Metadata-only on Postgres; supported by SQLite 3.25+. It does not rename
    # the constraint, hence the literal default name below.
    op.alter_column("words", "user_id", new_column_name="created_by_user_id")
    op.create_index("ix_words_created_by_user_id", "words", ["created_by_user_id"])

    if is_postgres:
        # CASCADE → RESTRICT. Deleting the owner of a shared deck must fail
        # loudly rather than destroy a class's vocabulary, and the database is
        # where that is enforced rather than hoped for. See CLAUDE.md.
        op.drop_constraint("words_user_id_fkey", "words", type_="foreignkey")
        op.create_foreign_key(
            "fk_words_created_by_user",
            "words",
            "users",
            ["created_by_user_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    for column in _PROGRESS_COLUMNS:
        op.drop_column("words", column)


def downgrade() -> None:
    is_postgres = op.get_bind().dialect.name == "postgresql"

    # Re-added nullable/defaulted so existing rows pass before the backfill.
    op.add_column("words", sa.Column("box", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("words", sa.Column("due_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "words", sa.Column("review_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("words", sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "words", sa.Column("lapse_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "words", sa.Column("consecutive_correct", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "words", sa.Column("first_reviewed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("words", sa.Column("mastered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("words", sa.Column("last_grade", sa.SmallInteger(), nullable=True))

    op.alter_column("words", "created_by_user_id", new_column_name="user_id")

    # Fold the creator's own progress back. Everyone else's goes with the table
    # below: a single-owner card has nowhere to put a second member's boxes.
    if is_postgres:
        op.execute(
            sa.text(
                """
                UPDATE words SET
                    box = p.box, due_at = p.due_at, review_count = p.review_count,
                    last_reviewed_at = p.last_reviewed_at, lapse_count = p.lapse_count,
                    consecutive_correct = p.consecutive_correct,
                    first_reviewed_at = p.first_reviewed_at, mastered_at = p.mastered_at,
                    last_grade = p.last_grade
                FROM word_progress p
                WHERE p.word_id = words.id AND p.user_id = words.user_id
                """
            )
        )
    else:
        op.execute(
            sa.text(
                """
                UPDATE words SET
                    box = (SELECT p.box FROM word_progress p
                           WHERE p.word_id = words.id AND p.user_id = words.user_id),
                    due_at = (SELECT p.due_at FROM word_progress p
                              WHERE p.word_id = words.id AND p.user_id = words.user_id)
                WHERE EXISTS (SELECT 1 FROM word_progress p
                              WHERE p.word_id = words.id AND p.user_id = words.user_id)
                """
            )
        )
    # Cards whose creator never studied them: box 1, due when the card last changed.
    op.execute(sa.text("UPDATE words SET due_at = updated_at WHERE due_at IS NULL"))
    op.alter_column("words", "due_at", nullable=False)

    if is_postgres:
        op.drop_constraint("fk_words_created_by_user", "words", type_="foreignkey")
        op.create_foreign_key(
            "words_user_id_fkey", "words", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )

    op.drop_index("ix_words_created_by_user_id", table_name="words")
    op.create_index("ix_words_user_due", "words", ["user_id", "due_at"])
    op.create_index(op.f("ix_words_due_at"), "words", ["due_at"])
    op.create_index(op.f("ix_words_user_id"), "words", ["user_id"])

    op.drop_index("ix_word_progress_deck_user", table_name="word_progress")
    op.drop_index("ix_word_progress_user_deck_due", table_name="word_progress")
    op.drop_index("ix_word_progress_user_due", table_name="word_progress")
    op.drop_table("word_progress")
    op.drop_index("ix_deck_members_user", table_name="deck_members")
    op.drop_table("deck_members")
