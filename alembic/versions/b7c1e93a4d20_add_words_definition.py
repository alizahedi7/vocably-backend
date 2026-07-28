"""add words.definition

The v7 "AI Card Magic" card back carries a dictionary definition of the chosen
sense alongside the meaning and example, and the Add-word screen now collects
it (``docs/ai-card-magic-contract.md``). Nullable and with no default: cards
created before this — and by clients that predate the field — simply have no
definition, which the client renders as an empty optional field.

Revision ID: b7c1e93a4d20
Revises: a1b2c3d4e5f6
Create Date: 2026-07-28 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c1e93a4d20"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("words", sa.Column("definition", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("words", "definition")
