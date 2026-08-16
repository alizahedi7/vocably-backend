"""add ``feedback_reports`` and ``ai_feedback`` — the two ways a learner tells us

Two tables rather than one polymorphic ``feedback``, because they answer
different questions and are read by different people. A report is a sentence
somebody wrote on purpose and is read one at a time; a rating is a thumb and is
only ever read as an aggregate. Sharing a table would mean a nullable message on
every rating, a nullable sense index on every report, and a discriminator at the
front of every query.

Two decisions here are worth stating, because both look like mistakes:

* **``user_id`` is nullable, with ``ON DELETE SET NULL``, on both.** A deleted
  account must stop being linked to anything, which this does. It must not
  silently withdraw an unfixed bug report, or delete a judgement about card
  quality that was never about the person who left it — which ``CASCADE`` would.
* **``ai_feedback.lookup_id`` is not a foreign key** to ``ai_lookup_entries``,
  though it holds exactly that table's ``entry_hash``. Entries are swept when a
  prompt version retires, and a verdict on a retired prompt is precisely the
  record worth keeping. The provenance columns beside it (``term``,
  ``prompt_version``, ``provider``, ``model``) are denormalised for the same
  reason: they are what the row still says once the entry it pointed at is gone.

Nothing is backfilled — there is nothing to backfill.

Revision ID: e7a3c15d4f80
Revises: a1d47f9c2b58
Create Date: 2026-08-16 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7a3c15d4f80"
down_revision: str | None = "a1d47f9c2b58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feedback_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("app_version", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("platform", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column("os_version", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("locale", sa.String(length=16), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_feedback_reports_user_id", "feedback_reports", ["user_id"])
    # The triage list is "newest first, optionally by kind", and that is the only
    # way this table is ever read.
    op.create_index("ix_feedback_reports_created_at", "feedback_reports", ["created_at"])
    op.create_index(
        "ix_feedback_reports_kind_created_at", "feedback_reports", ["kind", "created_at"]
    )

    op.create_table(
        "ai_feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("lookup_id", sa.String(length=64), nullable=False),
        sa.Column("sense_index", sa.Integer(), nullable=False),
        sa.Column("rating", sa.String(length=8), nullable=False),
        sa.Column("reason", sa.String(length=24), nullable=True),
        sa.Column("term", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("native_language", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("prompt_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=128), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        # The identity of a verdict: changing your mind moves this row rather
        # than adding one, which is what makes the endpoint safe to retry and
        # "how many people liked this card" a straight count.
        sa.UniqueConstraint(
            "user_id", "lookup_id", "sense_index", name="uq_ai_feedback_user_lookup_sense"
        ),
    )
    op.create_index("ix_ai_feedback_user_id", "ai_feedback", ["user_id"])
    # The roll-up: grouping key first, so scoring every rated sense is an index
    # scan rather than a sort of the table.
    op.create_index("ix_ai_feedback_lookup_sense", "ai_feedback", ["lookup_id", "sense_index"])


def downgrade() -> None:
    op.drop_index("ix_ai_feedback_lookup_sense", table_name="ai_feedback")
    op.drop_index("ix_ai_feedback_user_id", table_name="ai_feedback")
    op.drop_table("ai_feedback")
    op.drop_index("ix_feedback_reports_kind_created_at", table_name="feedback_reports")
    op.drop_index("ix_feedback_reports_created_at", table_name="feedback_reports")
    op.drop_index("ix_feedback_reports_user_id", table_name="feedback_reports")
    op.drop_table("feedback_reports")
