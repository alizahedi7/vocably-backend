"""add ``decks.copied_from_deck_id`` — so Explore can say "Saved"

A deck saved from Explore is a *copy*, and until now the copy recorded nothing
about where it came from. Explore therefore had no way to answer the one
question a browser asks second: "do I already have this?" — every card offered
"Save" whether or not the learner had taken it a minute earlier, and taking it
twice left two identical decks.

Remembering it on the device was the alternative and is the wrong one: it is
wrong after a reinstall, wrong on a second phone, and wrong in the PWA the
same account is signed into.

Provenance only. The copy stays independent — editing it changes nothing
upstream, and unpublishing or deleting the original nulls this column rather
than touching the copy, which is what ``SET NULL`` is for.

Nothing is backfilled: copies taken before this deploy have no recoverable
source, so they read as unsaved in Explore. The consequence is one stale
"Save" button on a deck someone already has, which the next save corrects.

Revision ID: c2f70a6b13d9
Revises: b1f4c7d2e903
Create Date: 2026-08-10 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2f70a6b13d9"
down_revision: str | None = "b1f4c7d2e903"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "decks",
        sa.Column("copied_from_deck_id", sa.Uuid(), nullable=True),
    )
    op.create_index("ix_decks_copied_from", "decks", ["copied_from_deck_id"])
    op.create_foreign_key(
        "fk_decks_copied_from",
        "decks",
        "decks",
        ["copied_from_deck_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_decks_copied_from", "decks", type_="foreignkey")
    op.drop_index("ix_decks_copied_from", table_name="decks")
    op.drop_column("decks", "copied_from_deck_id")
