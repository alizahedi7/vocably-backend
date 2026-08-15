"""a friend link waits for the other person to agree to it

``friend_links`` was built as a recency list rather than a social graph: adding
somebody wrote the row and that was the whole transaction, one-directional and
needing no consent, on the reasoning that it revealed nothing the sharer did not
already know because they had typed the handle themselves.

That reasoning stopped holding the moment the app grew a people search. A handle
is now *found*, not typed, so "I already knew it" is no longer true — and the
person on the other end was never told they had been added, had no way to refuse,
and no way to see it had happened. What the product needs is what the deck share
beside it already does: an offer, and an answer.

So a link gains ``accepted``. False is a request waiting on its recipient; true
is a friendship, and accepting writes the **reciprocal** row so both people hold
it. That is the part a flag alone would not give: a friendship somebody agreed to
is mutual by definition, where a recency list is not.

**Existing rows are backfilled to true.** They were made under the old rule,
where adding *was* the whole act — reading them as unanswered requests would
empty every learner's friends list into a pile of prompts about people they added
months ago, and asking somebody to re-approve a decision already taken is a worse
answer than honouring it. The reciprocal rows those links never had are not
invented either: they were one-directional when they were written, and this
migration does not get to claim consent nobody gave.

Revision ID: a1d47f9c2b58
Revises: d5c81b3f7e20
Create Date: 2026-08-15 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1d47f9c2b58"
down_revision: str | None = "d5c81b3f7e20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Added with a server default of true so the backfill above is the column's
    # own definition rather than a separate UPDATE that a partly-applied
    # migration could skip; the default is then dropped, because a *new* row
    # must state which it is and every path that writes one does.
    op.add_column(
        "friend_links",
        sa.Column("accepted", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("friend_links", "accepted", server_default=None)
    # The recipient's half of the question — "who has asked to add me?" — which
    # is a read on every foregrounded poll, and which no existing index covers:
    # the table is keyed (user_id, friend_user_id), so a lookup by the *second*
    # column is a scan.
    op.create_index(
        "ix_friend_links_incoming",
        "friend_links",
        ["friend_user_id", "accepted"],
    )


def downgrade() -> None:
    # A pending request has no representation in the old shape. Dropping the
    # column would silently promote every one of them to a friendship nobody
    # agreed to, so they go instead: the sender can ask again.
    op.execute(sa.text("DELETE FROM friend_links WHERE accepted = false"))
    op.drop_index("ix_friend_links_incoming", table_name="friend_links")
    op.drop_column("friend_links", "accepted")
