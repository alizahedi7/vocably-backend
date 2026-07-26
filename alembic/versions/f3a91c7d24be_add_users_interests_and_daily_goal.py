"""add users.interests and users.daily_goal

Profile setup v5 adds interest topics (themes AI examples/stories) and a daily
new-word goal. Existing rows get the defaults the app assumes: [] and 10.

Revision ID: f3a91c7d24be
Revises: 88e4ad207c34
Create Date: 2026-07-18 10:22:41.118204
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a91c7d24be"
down_revision: str | None = "88e4ad207c34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "interests",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column(
        "users",
        sa.Column("daily_goal", sa.Integer(), nullable=False, server_default=sa.text("10")),
    )


def downgrade() -> None:
    op.drop_column("users", "daily_goal")
    op.drop_column("users", "interests")
