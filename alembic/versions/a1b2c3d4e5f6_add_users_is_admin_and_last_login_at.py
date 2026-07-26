"""add users.is_admin and users.last_login_at

The admin dashboard needs a role to gate ``/admin/*`` behind (``is_admin``) and
a login timestamp to power the "Last Login" column and active-user metrics
(``last_login_at``, set on OTP/Google sign-in). Existing rows default to
non-admin with no recorded login (``NULL``, rendered "—" by the UI).

Revision ID: a1b2c3d4e5f6
Revises: f3a91c7d24be
Create Date: 2026-07-26 02:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f3a91c7d24be"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "users",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "is_admin")
