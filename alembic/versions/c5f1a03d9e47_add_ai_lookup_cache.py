"""add the shared AI lookup cache

Two tables backing the cross-user cache of ``POST /api/v1/ai/lookup`` responses
(``app/application/ports/lookup_cache.py``). ``ai_lookup_entries`` holds the
senses, keyed by resolved term; ``ai_lookup_aliases`` holds one small row per
distinct thing a learner typed, pointing at the entry it resolved to — so a
typo, a sentence and the correct spelling share one paid-for entry.

Neither table carries a ``user_id``: this is a dictionary the platform shares,
not user data. Both are additive, so the deploy is safe to roll back — dropping
them loses only cached content that can be re-bought.

Revision ID: c5f1a03d9e47
Revises: b7c1e93a4d20
Create Date: 2026-07-29 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5f1a03d9e47"
down_revision: str | None = "b7c1e93a4d20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_lookup_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        # sha256 hex of the cache key: fixed-width and collation-neutral, so the
        # hot index behaves the same for every script the app accepts.
        sa.Column("entry_hash", sa.String(length=64), nullable=False),
        sa.Column("term", sa.String(length=255), nullable=False),
        sa.Column("native_language", sa.String(length=64), nullable=False),
        sa.Column("age_bucket", sa.String(length=16), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=128), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entry_hash", name="uq_ai_lookup_entries_entry_hash"),
    )
    op.create_index("ix_ai_lookup_entries_prompt_version", "ai_lookup_entries", ["prompt_version"])

    op.create_table(
        "ai_lookup_aliases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("alias_hash", sa.String(length=64), nullable=False),
        sa.Column("normalized_input", sa.String(length=255), nullable=False),
        sa.Column("native_language", sa.String(length=64), nullable=False),
        sa.Column("age_bucket", sa.String(length=16), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        # NULL for `unsupported`, which has no senses to point at.
        sa.Column("entry_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("notice", sa.Text(), nullable=True),
        sa.Column("resolved_term", sa.String(length=255), nullable=False),
        # Set only for `unsupported`; real words do not expire.
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["entry_id"], ["ai_lookup_entries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alias_hash", name="uq_ai_lookup_aliases_alias_hash"),
    )
    op.create_index("ix_ai_lookup_aliases_entry_id", "ai_lookup_aliases", ["entry_id"])
    op.create_index("ix_ai_lookup_aliases_prompt_version", "ai_lookup_aliases", ["prompt_version"])
    op.create_index("ix_ai_lookup_aliases_expires_at", "ai_lookup_aliases", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_lookup_aliases_expires_at", table_name="ai_lookup_aliases")
    op.drop_index("ix_ai_lookup_aliases_prompt_version", table_name="ai_lookup_aliases")
    op.drop_index("ix_ai_lookup_aliases_entry_id", table_name="ai_lookup_aliases")
    op.drop_table("ai_lookup_aliases")
    op.drop_index("ix_ai_lookup_entries_prompt_version", table_name="ai_lookup_entries")
    op.drop_table("ai_lookup_entries")
