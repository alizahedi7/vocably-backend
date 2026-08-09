"""ORM models for the lexicon — durable, shared vocabulary knowledge.

Three tables rather than one, and the split is the cost model:

``lexemes``
    The headword. One row per ``(language, lemma)``, and that unique constraint
    is the concurrency primitive: two workers racing on a brand-new word both
    insert, one loses harmlessly, and neither corrupts anything.

``lexeme_senses``
    The English half — definition, example, part of speech, sense label. This is
    the expensive half and the one a dictionary can supply verbatim. Keyed
    ``(lexeme, sense_key, register)`` so enrichment can append a missing sense
    without a fuzzy match and a redelivered write is a no-op.

``lexeme_sense_translations``
    The learner-facing headline, one row per language. Split out so that adding
    a second native language re-buys short translations rather than the corpus.

No ``user_id`` reaches any of them, exactly as with the lookup cache: this is a
dictionary the whole platform shares, not user data. Unlike the cache, it is
**durable** — a deck card points at a sense id, so senses must outlive the prompt
version that wrote them. See ``docs/prebuilt-deck-pipeline.md``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.infrastructure.db.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class LexemeModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "lexemes"
    __table_args__ = (
        # The dedup key and the lookup path's only entry point. Unique so a race
        # between a user lookup and a deck build costs one wasted provider call
        # rather than two half-populated headwords.
        UniqueConstraint("language", "lemma", name="uq_lexemes_language_lemma"),
    )

    #: ``normalize_lookup_input`` applied. Case-folded, so ``Polish``/``polish``
    #: share a lexeme and both readings live as senses of it.
    lemma: Mapped[str] = mapped_column(String(255), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    #: What a learner sees — the provider's or dictionary's own casing.
    display_term: Mapped[str] = mapped_column(String(255), nullable=False)

    #: IPA. NULL means *not looked up yet*; ``""`` means *the dictionary has none
    #: for this word*. Both render as nothing, and conflating them would make the
    #: backfill re-ask the same permanent misses forever.
    phonetic: Mapped[str | None] = mapped_column(String(200))


class LexemeSenseModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "lexeme_senses"
    __table_args__ = (
        UniqueConstraint("lexeme_id", "sense_key", "register", name="uq_lexeme_senses_key"),
        Index("ix_lexeme_senses_lexeme_position", "lexeme_id", "position"),
        # The review queue and the staleness report are the only scans this
        # table sees; everything else arrives by lexeme id.
        Index("ix_lexeme_senses_status", "status"),
        Index("ix_lexeme_senses_content_version", "content_version"),
    )

    lexeme_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lexemes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    #: ``slug(part_of_speech):slug(context)`` — see ``sense_key_for``.
    sense_key: Mapped[str] = mapped_column(String(80), nullable=False)
    #: child | teen | adult. The same three buckets the lookup cache collapses
    #: age to, because they are the only ones that change the wording.
    register: Mapped[str] = mapped_column(String(16), nullable=False, default="adult")
    #: Provider order, most common first. **Never renumbered** — a published deck
    #: may already point at position 2.
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    part_of_speech: Mapped[str] = mapped_column(String(32), nullable=False)
    context: Mapped[str] = mapped_column(String(120), nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    example: Mapped[str] = mapped_column(Text, nullable=False, default="")

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    #: ``deps._effective_prompt_version()`` at write time — the whole pipeline's
    #: version, not one prompt's. A bump marks this stale; it never deletes it.
    content_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="lookup")


class LexemeSenseTranslationModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "lexeme_sense_translations"
    __table_args__ = (
        UniqueConstraint(
            "sense_id", "native_language", name="uq_lexeme_sense_translations_language"
        ),
    )

    sense_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lexeme_senses.id", ondelete="CASCADE"), index=True, nullable=False
    )
    #: Spelled as ``users.native_language`` spells it ("Persian"), not as a
    #: BCP-47 tag: nothing in the app speaks tags, and a second spelling of the
    #: same fact is a second thing that can disagree.
    native_language: Mapped[str] = mapped_column(String(64), nullable=False)
    native_meaning: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    content_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
