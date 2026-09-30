"""add the reader: books, their text, reading positions, passage translations

Five tables and no change to any existing one. The reader reuses the lexicon
for vocabulary — there is deliberately no ``vocabulary_cache`` table (see
``docs/adr/0001-the-reader-reads-the-lexicon.md``) — so everything here is
either public-domain text, one learner's place in it, or a translation of it.

Nothing is backfilled: there are no books until one is ingested, and ingest
never publishes.

Revision ID: c4a8e1f7d392
Revises: b8e2f47c19d3
Create Date: 2026-09-29 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c4a8e1f7d392"
down_revision: str | None = "b8e2f47c19d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "books",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("slug", sa.String(160), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("author", sa.String(300), nullable=False, server_default=""),
        sa.Column("language", sa.String(16), nullable=False, server_default="en"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("cover_url", sa.String(500), nullable=False, server_default=""),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("source_id", sa.String(300), nullable=False),
        sa.Column("source_url", sa.String(500), nullable=False, server_default=""),
        sa.Column("rights", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "extra", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("total_chapters", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_words", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("source", "source_id", name="uq_books_source"),
        sa.UniqueConstraint("slug", name="uq_books_slug"),
    )
    op.create_index("ix_books_public_language", "books", ["is_public", "language", "published_at"])

    op.create_table(
        "book_chapters",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "book_id", sa.Uuid(), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("index", sa.SmallInteger(), nullable=False),
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
        sa.Column("part_title", sa.String(300), nullable=False, server_default=""),
        sa.Column("word_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("block_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("words_before", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("book_id", "index", name="uq_book_chapters_index"),
    )

    op.create_table(
        "book_blocks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "chapter_id",
            sa.Uuid(),
            sa.ForeignKey("book_chapters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="paragraph"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("words_before", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("chapter_id", "position", name="uq_book_blocks_position"),
    )
    op.create_index("ix_book_blocks_text_hash", "book_blocks", ["text_hash"])

    op.create_table(
        "book_progress",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "book_id", sa.Uuid(), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "chapter_id",
            sa.Uuid(),
            sa.ForeignKey("book_chapters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "block_id",
            sa.Uuid(),
            sa.ForeignKey("book_blocks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("char_offset", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("percent", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("user_id", "book_id", name="pk_book_progress"),
    )
    op.create_index("ix_book_progress_user_updated", "book_progress", ["user_id", "updated_at"])

    op.create_table(
        "passage_translations",
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("target_language", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("translation", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False, server_default=""),
        sa.Column("model", sa.String(128), nullable=False, server_default=""),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint(
            "text_hash", "target_language", "prompt_version", name="pk_passage_translations"
        ),
    )


def downgrade() -> None:
    op.drop_table("passage_translations")
    op.drop_index("ix_book_progress_user_updated", table_name="book_progress")
    op.drop_table("book_progress")
    op.drop_index("ix_book_blocks_text_hash", table_name="book_blocks")
    op.drop_table("book_blocks")
    op.drop_table("book_chapters")
    op.drop_index("ix_books_public_language", table_name="books")
    op.drop_table("books")
