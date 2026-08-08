"""add deck_units and words.unit_id

A deck may group its cards into units/lessons; most never will. The rule the
client is built on is that a deck with no units renders exactly as it did
before the feature existed, and a card may belong to *no* unit — there is
deliberately no "default" or "uncategorised" unit, which is why ``unit_id`` is
nullable rather than pointing at a placeholder row.

``ON DELETE SET NULL`` on ``words.unit_id`` **is** the product rule, not a
default: deleting a unit keeps its cards and drops them back into the deck. The
client shows a toast saying how many came loose and asks for no confirmation,
precisely because nothing is lost. A cascade here would delete a lesson's
vocabulary along with its heading.

Order is ``position``, not ``name``: "Unit 10" sorts between 1 and 2
alphabetically. The index is not unique — a UNIQUE (deck_id, position) needs a
deferred constraint or a two-pass update to reorder, and server-assigned gaps
are simpler and enough.

Purely additive: existing rows get ``unit_id = NULL``, which is the normal
state, and clients that never send the field keep working unchanged.

Revision ID: c9e4f6a03d25
Revises: b8d3e5f92c14
Create Date: 2026-08-06 13:05:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9e4f6a03d25"
down_revision: str | None = "b8d3e5f92c14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deck_units",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("deck_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_deck_units"),
        sa.ForeignKeyConstraint(
            ["deck_id"], ["decks.id"], name="fk_deck_units_deck", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_deck_units_deck_position", "deck_units", ["deck_id", "position"])

    op.add_column("words", sa.Column("unit_id", sa.Uuid(), nullable=True))
    op.create_index("ix_words_unit_id", "words", ["unit_id"])
    op.create_foreign_key(
        "fk_words_unit", "words", "deck_units", ["unit_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint("fk_words_unit", "words", type_="foreignkey")
    op.drop_index("ix_words_unit_id", table_name="words")
    op.drop_column("words", "unit_id")
    op.drop_index("ix_deck_units_deck_position", table_name="deck_units")
    op.drop_table("deck_units")
