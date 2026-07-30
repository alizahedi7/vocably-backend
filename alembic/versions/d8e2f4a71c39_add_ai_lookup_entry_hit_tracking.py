"""add hit tracking to ai_lookup_entries

Admins have no visibility into which cached words are actually reused. Adds
``hit_count`` (incremented on every cache hit that resolves to this entry) and
``last_accessed_at`` (touched alongside it) so the new admin cache-monitoring
endpoints can report real reuse instead of a proxy metric. Additive and
backward compatible — existing rows default to ``hit_count = 0`` and
``last_accessed_at = NULL`` (never yet re-served since this column existed).

Revision ID: d8e2f4a71c39
Revises: c5f1a03d9e47
Create Date: 2026-07-30 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8e2f4a71c39"
down_revision: str | None = "c5f1a03d9e47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_lookup_entries",
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ai_lookup_entries",
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_lookup_entries", "last_accessed_at")
    op.drop_column("ai_lookup_entries", "hit_count")
