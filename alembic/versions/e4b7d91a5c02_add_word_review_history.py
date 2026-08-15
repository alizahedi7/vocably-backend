"""add the append-only word review history

Creates ``word_reviews`` — one immutable row per graded review — and the summary
counters on ``words`` that let the product read difficulty and time-to-mastery
without touching the event log.

On Postgres the table is **partitioned by month on ``reviewed_at`` from the
start**. This is the whole point of doing it now: converting a table with
hundreds of millions of rows to a partitioned one later means an offline rewrite
or a delicate attach-and-swap, while doing it on an empty table costs nothing.
Retention then becomes ``DROP TABLE`` per month — instant, and it reclaims the
space immediately, where a bulk ``DELETE`` would leave a bloated heap and hours
of vacuum behind it.

Three pieces make that work:

* ``ensure_word_reviews_partition(date)`` — idempotent, creates the partition
  covering a month. It lives in the database rather than in Python so the
  migration, the maintenance job and any operator at a psql prompt all use one
  definition (``app/scripts/partition_word_reviews.py``).
* A rolling window of monthly partitions, pre-created 12 months ahead.
* ``word_reviews_default``, a catch-all so that an insert can never fail just
  because partition maintenance lapsed. It is expected to stay empty; the
  maintenance script reports it as an error if it is not.

Other backends (the SQLite test suite) get a single plain table. The Postgres
primary key is the composite ``(id, reviewed_at)`` because a partitioned table
must include its partition key in every unique constraint; the ORM model
declares ``id`` alone, which Alembic does not diff.

Additive and safe to roll back: the new columns are nullable or defaulted, and
``downgrade`` drops the table with every partition attached to it.

Revision ID: e4b7d91a5c02
Revises: d8e2f4a71c39
Create Date: 2026-07-30 09:00:00.000000
"""

from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4b7d91a5c02"
down_revision: str | None = "d8e2f4a71c39"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Months of partitions pre-created ahead of the deploy. The maintenance job
#: keeps this window rolling; the head start means a lapsed job has a year of
#: slack before the default partition ever takes a row.
INITIAL_MONTHS_AHEAD = 12

_ENSURE_PARTITION_FN = """
CREATE OR REPLACE FUNCTION ensure_word_reviews_partition(target date)
RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
    start_date date := date_trunc('month', target)::date;
    end_date   date := (date_trunc('month', target) + interval '1 month')::date;
    part_name  text := 'word_reviews_' || to_char(start_date, 'YYYYMM');
BEGIN
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF word_reviews FOR VALUES FROM (%L) TO (%L)',
        part_name, start_date, end_date
    );
    RETURN part_name;
END;
$$;
"""

_COLUMNS = (
    sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column("user_id", sa.Uuid(), nullable=False),
    sa.Column("word_id", sa.Uuid(), nullable=False),
    # No FK: a deck the card has since left, or one that was deleted, must not
    # rewrite or erase a review that already happened.
    sa.Column("deck_id", sa.Uuid(), nullable=False),
    sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
    # Grades and boxes are stored as their small-integer encodings
    # (``ReviewGrade.ordinal`` / ``LeitnerBox`` values): two bytes rather than
    # seven, and ordered, on the largest table in the schema.
    sa.Column("grade", sa.SmallInteger(), nullable=False),
    sa.Column("box_before", sa.SmallInteger(), nullable=False),
    sa.Column("box_after", sa.SmallInteger(), nullable=False),
    sa.Column("elapsed_seconds", sa.Integer(), nullable=True),
    sa.Column("overdue_seconds", sa.Integer(), nullable=True),
    sa.Column("latency_ms", sa.Integer(), nullable=True),
    sa.Column("session_id", sa.Uuid(), nullable=True),
)

_CHECKS = (
    sa.CheckConstraint("grade BETWEEN 0 AND 3", name="ck_word_reviews_grade"),
    sa.CheckConstraint("box_before BETWEEN 1 AND 5", name="ck_word_reviews_box_before"),
    sa.CheckConstraint("box_after BETWEEN 1 AND 5", name="ck_word_reviews_box_after"),
)


def _months_from(start: date, count: int) -> list[date]:
    months = []
    year, month = start.year, start.month
    for _ in range(count):
        months.append(date(year, month, 1))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def upgrade() -> None:
    is_postgres = op.get_bind().dialect.name == "postgresql"

    # ── words: review summary counters ───────────────────────
    # server_default backfills existing rows without a table rewrite; the
    # application always supplies these, so the defaults are dropped afterwards
    # to keep the column contract explicit rather than half-implicit.
    op.add_column(
        "words", sa.Column("lapse_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "words", sa.Column("consecutive_correct", sa.Integer(), nullable=False, server_default="0")
    )
    # NULL on cards that predate this migration: their first review really is
    # unknown, and inventing created_at would poison every time-to-mastery
    # figure computed from it.
    op.add_column(
        "words", sa.Column("first_reviewed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("words", sa.Column("mastered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("words", sa.Column("last_grade", sa.SmallInteger(), nullable=True))
    op.alter_column("words", "lapse_count", server_default=None)
    op.alter_column("words", "consecutive_correct", server_default=None)

    # ── word_reviews ─────────────────────────────────────────
    table_kwargs = {}
    if is_postgres:
        # A partitioned table must carry its partition key in every unique
        # constraint, so the key is (id, reviewed_at) rather than id alone.
        primary_key = sa.PrimaryKeyConstraint("id", "reviewed_at", name="pk_word_reviews")
        table_kwargs["postgresql_partition_by"] = "RANGE (reviewed_at)"
    else:
        primary_key = sa.PrimaryKeyConstraint("id", name="pk_word_reviews")

    op.create_table(
        "word_reviews",
        *_COLUMNS,
        primary_key,
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_word_reviews_user", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["word_id"], ["words.id"], name="fk_word_reviews_word", ondelete="CASCADE"
        ),
        *_CHECKS,
        **table_kwargs,
    )

    # Exactly two secondary indexes. Each one roughly adds a random write per
    # insert, and write amplification — not the heap — is what makes an event
    # table expensive. deck_id gets none: per-deck analytics belong on rollups.
    #
    # Both are ASC; Postgres scans a B-tree backwards at no cost, so
    # "latest first" within a word_id/user_id group needs no DESC index.
    op.create_index("ix_word_reviews_word", "word_reviews", ["word_id", "reviewed_at"])
    op.create_index("ix_word_reviews_user_reviewed", "word_reviews", ["user_id", "reviewed_at"])

    if is_postgres:
        op.execute(_ENSURE_PARTITION_FN)
        op.execute(
            "CREATE TABLE IF NOT EXISTS word_reviews_default PARTITION OF word_reviews DEFAULT"
        )
        for month in _months_from(date.today().replace(day=1), INITIAL_MONTHS_AHEAD):
            op.execute(sa.text("SELECT ensure_word_reviews_partition(:m)").bindparams(m=month))


def downgrade() -> None:
    # Dropping the parent drops every attached partition with it.
    op.drop_index("ix_word_reviews_user_reviewed", table_name="word_reviews")
    op.drop_index("ix_word_reviews_word", table_name="word_reviews")
    op.drop_table("word_reviews")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS ensure_word_reviews_partition(date)")

    op.drop_column("words", "last_grade")
    op.drop_column("words", "mastered_at")
    op.drop_column("words", "first_reviewed_at")
    op.drop_column("words", "consecutive_correct")
    op.drop_column("words", "lapse_count")
