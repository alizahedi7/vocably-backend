"""add sentence_meanings: what a word means in one sentence of a book

The durable twin of the reader's Redis memo, stored under the same rule as a
passage translation: only for sentences of a stored book, never for text a
learner brought. No user id. Keyed by prompt version, so a better prompt
retires old rows by never matching them.

Revision ID: d2f5c8a1e764
Revises: c4a8e1f7d392
Create Date: 2026-09-30 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d2f5c8a1e764"
down_revision: str | None = "c4a8e1f7d392"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sentence_meanings",
        sa.Column("sentence_hash", sa.String(64), nullable=False),
        sa.Column("word", sa.String(120), nullable=False),
        sa.Column("native_language", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("lemma", sa.String(120), nullable=False),
        sa.Column("part_of_speech", sa.String(32), nullable=False, server_default=""),
        sa.Column("context", sa.String(120), nullable=False, server_default=""),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("native_meaning", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False, server_default=""),
        sa.Column("model", sa.String(128), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint(
            "sentence_hash",
            "word",
            "native_language",
            "prompt_version",
            name="pk_sentence_meanings",
        ),
    )


def downgrade() -> None:
    op.drop_table("sentence_meanings")
