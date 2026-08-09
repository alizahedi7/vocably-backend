"""add the shared lexicon and the deck build pipeline

Five tables and one column, in two halves.

**The lexicon** (``lexemes``, ``lexeme_senses``, ``lexeme_sense_translations``)
is durable shared vocabulary knowledge — deliberately *not* the same thing as
``ai_lookup_entries``. That cache is keyed by prompt version and swept when a
prompt changes; this is keyed by the lemma, has a stable id per sense that a deck
card can reference, and survives a prompt bump as stale rather than deleted.
Splitting the learner-facing headline into its own table is what makes adding a
second native language cost a translation pass instead of a second corpus.

**The build pipeline** (``deck_build_jobs``, ``deck_build_items``) is the plan
for turning a template into a deck. It lives in the database rather than in a
queue because that makes resume, idempotence and progress reporting the same
mechanism: ``UNIQUE (job_id, position)`` plus a conditional claim means a
redelivered Celery message finds finished work already finished.

Nothing here is backfilled and nothing existing changes behaviour: ``lexemes`` is
empty until the first lookup or build writes to it, and ``words.lexeme_sense_id``
is NULL on every card that exists today and on every card typed by hand
afterwards. A separate one-off script seeds the lexicon from the cache rows the
user base has already paid for.

Revision ID: b1f4c7d2e903
Revises: a3d7c0f21b58
Create Date: 2026-08-09 10:22:41.882104
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1f4c7d2e903"
down_revision: str | None = "a3d7c0f21b58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: JSONB where it exists, JSON on SQLite — matching the model definition, which
#: has to run against both.
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "lexemes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("lemma", sa.String(length=255), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False, server_default="en"),
        sa.Column("display_term", sa.String(length=255), nullable=False),
        # NULL means "not looked up yet"; '' means "the dictionary has none".
        sa.Column("phonetic", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        # The deduplication key, and the primitive that makes a race between a
        # user lookup and a deck build cost one wasted provider call rather than
        # two half-populated headwords.
        sa.UniqueConstraint("language", "lemma", name="uq_lexemes_language_lemma"),
    )

    op.create_table(
        "lexeme_senses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "lexeme_id",
            sa.Uuid(),
            sa.ForeignKey("lexemes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sense_key", sa.String(length=80), nullable=False),
        sa.Column("register", sa.String(length=16), nullable=False, server_default="adult"),
        sa.Column("position", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("part_of_speech", sa.String(length=32), nullable=False),
        sa.Column("context", sa.String(length=120), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("example", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="auto"),
        sa.Column("content_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="lookup"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        # Enrichment's idempotence: appending a sense the lexeme already holds is
        # a no-op rather than a duplicate card.
        sa.UniqueConstraint("lexeme_id", "sense_key", "register", name="uq_lexeme_senses_key"),
    )
    op.create_index("ix_lexeme_senses_lexeme_id", "lexeme_senses", ["lexeme_id"])
    op.create_index("ix_lexeme_senses_lexeme_position", "lexeme_senses", ["lexeme_id", "position"])
    op.create_index("ix_lexeme_senses_status", "lexeme_senses", ["status"])
    op.create_index("ix_lexeme_senses_content_version", "lexeme_senses", ["content_version"])

    op.create_table(
        "lexeme_sense_translations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "sense_id",
            sa.Uuid(),
            sa.ForeignKey("lexeme_senses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("native_language", sa.String(length=64), nullable=False),
        sa.Column("native_meaning", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="auto"),
        sa.Column("content_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "sense_id", "native_language", name="uq_lexeme_sense_translations_language"
        ),
    )
    op.create_index(
        "ix_lexeme_sense_translations_sense_id", "lexeme_sense_translations", ["sense_id"]
    )

    op.create_table(
        "deck_build_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("template_slug", sa.String(length=80), nullable=False),
        sa.Column("template_version", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("template_hash", sa.String(length=64), nullable=False, server_default=""),
        # SET NULL: deleting a deck must not destroy the record of what building
        # it cost.
        sa.Column("deck_id", sa.Uuid(), sa.ForeignKey("decks.id", ondelete="SET NULL")),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="planned"),
        sa.Column("content_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "native_language", sa.String(length=64), nullable=False, server_default="Persian"
        ),
        sa.Column("register", sa.String(length=16), nullable=False, server_default="adult"),
        # Copied from the template at plan time rather than re-read during the
        # build: the plan lives here, so it must stay buildable if the file moves.
        sa.Column("category", sa.String(length=32), nullable=False, server_default="general"),
        sa.Column("strategies", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("items_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lexemes_reused", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lexemes_generated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("senses_enriched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ai_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_deck_build_jobs_deck_id", "deck_build_jobs", ["deck_id"])
    op.create_index(
        "ix_deck_build_jobs_created_by_user_id", "deck_build_jobs", ["created_by_user_id"]
    )
    op.create_index("ix_deck_build_jobs_slug_state", "deck_build_jobs", ["template_slug", "state"])

    op.create_table(
        "deck_build_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("deck_build_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("unit_label", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("unit_position", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("source_term", sa.String(length=255), nullable=False),
        sa.Column("normalized", sa.String(length=255), nullable=False),
        sa.Column("hint", _JSON, nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("lexeme_id", sa.Uuid(), sa.ForeignKey("lexemes.id", ondelete="SET NULL")),
        sa.Column("sense_id", sa.Uuid(), sa.ForeignKey("lexeme_senses.id", ondelete="SET NULL")),
        sa.Column("word_id", sa.Uuid(), sa.ForeignKey("words.id", ondelete="SET NULL")),
        sa.Column("selection", sa.String(length=24)),
        sa.Column("selection_score", sa.Float()),
        sa.Column("attempts", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("enriched", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        # Resume key and dedup key at once: the plan is written once and every
        # later step is a state transition, so redelivery cannot double a card.
        sa.UniqueConstraint("job_id", "position", name="uq_deck_build_items_position"),
    )
    op.create_index(
        "ix_deck_build_items_claim",
        "deck_build_items",
        ["job_id", "state", "next_attempt_at"],
    )

    op.add_column(
        "words",
        sa.Column(
            "lexeme_sense_id",
            sa.Uuid(),
            sa.ForeignKey("lexeme_senses.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_words_lexeme_sense_id", "words", ["lexeme_sense_id"])


def downgrade() -> None:
    op.drop_index("ix_words_lexeme_sense_id", table_name="words")
    op.drop_column("words", "lexeme_sense_id")
    op.drop_table("deck_build_items")
    op.drop_table("deck_build_jobs")
    op.drop_table("lexeme_sense_translations")
    op.drop_table("lexeme_senses")
    op.drop_table("lexemes")
