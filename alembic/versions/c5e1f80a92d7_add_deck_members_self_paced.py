"""add ``deck_members.self_paced`` — decks whose words wait to be started

A learner who saves "504 Essential Words" from Explore, or is handed a
1100-word class deck, used to receive all of it at once: progress rows are
lazy, and a *missing* row reads as box 1 / due now, so five hundred cards
landed in one day's review queue. The queue is the product; burying it is how
someone stops opening the app.

This column flips what a missing row means, per membership:

* ``self_paced = false`` (the default, and every deck anyone builds themselves)
  — unchanged. Adding a card is the act of deciding to learn it, so a word with
  no progress row is new, due now, and in the boxes.
* ``self_paced = true`` — a word with no progress row is **not started**: not
  due, not counted, not in a session, until the learner starts it. Set when a
  deck arrives from somewhere else: an Explore copy, an accepted share, a
  person added to a deck, or an invite code redeemed.

Nothing is backfilled, deliberately. Every existing membership stays
``false``, so no one's due queue, streak or memory strength changes the night
this deploys; self-pacing applies to decks saved from here on. Recomputing
history would silently take words *out* of people's queues, which is a worse
surprise than the one being fixed.

Revision ID: c5e1f80a92d7
Revises: f4b8d2e61a97
Create Date: 2026-08-08 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5e1f80a92d7"
down_revision: str | None = "f4b8d2e61a97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "deck_members",
        sa.Column("self_paced", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("deck_members", "self_paced")
