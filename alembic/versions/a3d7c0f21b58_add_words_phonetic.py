"""add ``words.phonetic`` — the IPA shown on the card front

The dictionary already hands back a transcription on every ``/ai/lookup``
(``LookupResult.phonetic``), and it was thrown away the moment the learner
saved the card: nothing on ``words`` could hold it, so the one place it is
useful — the front of the card, next to the speak button — never saw it.

Nullable with no default, and null is the ordinary case rather than an error:
roughly a third of words have no IPA in the source at all, every hand-written
card starts without one, and every row that existed before this migration is
null until ``vocably.ai.backfill_phonetics`` reaches it. Clients render it as
optional and show nothing when it is absent, so no backfill is required for
this column to be safe to deploy.

Bounded to 200 characters rather than ``Text``: it is one short transcription
of one card front, never prose.

Revision ID: a3d7c0f21b58
Revises: c5e1f80a92d7
Create Date: 2026-08-08 09:14:22.100311
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3d7c0f21b58"
down_revision: str | None = "c5e1f80a92d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("words", sa.Column("phonetic", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("words", "phonetic")
