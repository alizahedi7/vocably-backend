"""add ``decks.language`` and ``deck_build_jobs.language``

The language a deck's headwords are written in (ISO 639-1). Pre-built decks
were English-only; a Spanish or German template needs the build to look its
words up as Spanish or German, keep them apart from English lexemes of the same
spelling, and skip the English-only dictionary and IPA.

Both columns default to ``'en'``, which is what every existing row is, so
nothing is backfilled and nothing already built changes.

Revision ID: f3a8c61d7e42
Revises: e5b7a2c94d18
Create Date: 2026-11-11 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a8c61d7e42"
down_revision: str | None = "e5b7a2c94d18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "decks",
        sa.Column("language", sa.String(16), nullable=False, server_default="en"),
    )
    op.add_column(
        "deck_build_jobs",
        sa.Column("language", sa.String(16), nullable=False, server_default="en"),
    )


def downgrade() -> None:
    op.drop_column("deck_build_jobs", "language")
    op.drop_column("decks", "language")
