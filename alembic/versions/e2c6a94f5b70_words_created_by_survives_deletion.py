"""let a card outlive the account that created it

``a7f2c8b41d63`` made ``words.created_by_user_id`` ``ON DELETE RESTRICT``,
reasoning that deleting the owner of a class deck must not destroy it. That was
right about the goal and wrong about the mechanism: Postgres checks RESTRICT
*immediately*, before the ``decks`` cascade has removed the user's own words, so
the constraint fired for **every** account that had ever created a card —
including a solo learner with no sharing at all. No account could be deleted,
which is a worse failure than the one it was preventing, and one that would
have surfaced first as an unanswerable erasure request.

``SET NULL`` is the correct shape, and it is the same reasoning that already
governs ``deck_members.invited_by_user_id``: the column is *attribution*, and
attribution is exactly the thing that may be lost when a person leaves. A word
an editor added to a teacher's deck belongs to the deck, not to the editor;
their account going away must leave the class's vocabulary standing, with the
card simply no longer crediting anyone.

What this does **not** try to express is the account-deletion policy itself —
"block while the user owns a deck shared with other people". A foreign key
cannot see whether a deck is shared, and the column that would destroy the
class deck is ``decks.user_id``, not this one. That rule belongs in the
application, at the point an account-deletion flow exists; see CLAUDE.md.

Revision ID: e2c6a94f5b70
Revises: d1a5b7c34e86
Create Date: 2026-08-06 17:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2c6a94f5b70"
down_revision: str | None = "d1a5b7c34e86"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("words", "created_by_user_id", existing_type=sa.Uuid(), nullable=True)
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("fk_words_created_by_user", "words", type_="foreignkey")
        op.create_foreign_key(
            "fk_words_created_by_user",
            "words",
            "users",
            ["created_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    # Cards whose creator is gone cannot be given one back; attribute them to
    # the deck's creator so the column can be NOT NULL again.
    op.execute(
        sa.text(
            """
            UPDATE words SET created_by_user_id = d.user_id
            FROM decks d
            WHERE d.id = words.deck_id AND words.created_by_user_id IS NULL
            """
        )
    )
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("fk_words_created_by_user", "words", type_="foreignkey")
        op.create_foreign_key(
            "fk_words_created_by_user",
            "words",
            "users",
            ["created_by_user_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.alter_column("words", "created_by_user_id", existing_type=sa.Uuid(), nullable=False)
