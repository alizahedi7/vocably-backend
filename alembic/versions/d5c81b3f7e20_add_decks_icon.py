"""add ``decks.icon`` — a logo for the decks Vocably builds

Every deck in Explore draws the same badge: its first letter on a coloured
square. That is right for a deck a learner made and wrong for a course, where
the deck *is* a recognisable thing and "5" tells nobody it is 504.

A slug rather than a URL, deliberately. The badge is a fixed square on a
fixed-height card, and it has to be drawn on the first frame — an image fetched
over the network arrives late, arrives at a different size, and does not arrive
at all on a train. The client ships the asset and looks it up by this slug; one
it does not recognise falls back to the initial, so a template may name an icon
before any released client has it.

Empty for every deck that exists today, which is exactly what they already
render. Nothing is backfilled and nothing needs to be: the column is populated
by planning a build from a template that names one, or by ``deck-sync-meta``
for a deck already built.

Revision ID: d5c81b3f7e20
Revises: c2f70a6b13d9
Create Date: 2026-08-10 15:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5c81b3f7e20"
down_revision: str | None = "c2f70a6b13d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "decks",
        sa.Column("icon", sa.String(length=40), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("decks", "icon")
