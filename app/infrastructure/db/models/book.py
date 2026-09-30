"""ORM models for the reader: books, their text, and where each learner is.

Three tables hold the text and two hold what surrounds it. The split follows
the access pattern, not normalisation for its own sake:

``books``
    The catalogue row. What the library lists, and the publication gate.
``book_chapters``
    The table of contents. Read whole on opening a book (a novel has forty
    rows); never carries text.
``book_blocks``
    The text, one row per paragraph. Read by chapter, in ``position`` order,
    and pointed at by reading positions and lookups. A row's ``text`` is
    **immutable** after ingest — every offset a client ever sends assumes it.
``book_progress``
    One learner's place in one book. The only per-user table here.
``passage_translations``
    A shared, impersonal cache of translated paragraphs, keyed by text hash.
    Postgres rather than Redis-only because a translation costs real money
    and a novel is read for years.
``sentence_meanings``
    The same, for what one word means in one sentence of a book.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.database import Base
from app.infrastructure.db.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.infrastructure.db.types import UTCDateTime

#: JSONB on Postgres, plain JSON under the SQLite test run — the same variant
#: ``deck_build_items.hint`` uses.
ExtraPayload = JSON().with_variant(JSONB(), "postgresql")


class BookModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "books"
    __table_args__ = (
        # One row per edition of one source. Re-running an ingest finds this
        # row and compares content hashes instead of writing a twin.
        UniqueConstraint("source", "source_id", name="uq_books_source"),
        UniqueConstraint("slug", name="uq_books_slug"),
        # The catalogue query: public books, newest first, optionally by language.
        Index("ix_books_public_language", "is_public", "language", "published_at"),
    )

    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    author: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    #: ISO 639-1 of the text. "en" for everything the pipeline ingests today;
    #: a column rather than a constant so a Persian reader can exist later
    #: without a migration.
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: A URL, unlike ``decks.icon``, because a cover is fetched once on a
    #: catalogue screen that already scrolls — the first-frame argument for a
    #: shipped asset does not apply. Empty when the source had none.
    cover_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    source: Mapped[str] = mapped_column(String(24), nullable=False)
    source_id: Mapped[str] = mapped_column(String(300), nullable=False)
    source_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    rights: Mapped[str] = mapped_column(Text, nullable=False, default="")
    extra: Mapped[dict[str, Any]] = mapped_column(ExtraPayload, nullable=False, default=dict)

    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    total_chapters: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_words: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: The only answer to "is this book in the library?" — the same rule as
    #: ``decks.is_public``. Ingest never sets it; an admin does, deliberately,
    #: after reading the chapter list the parser produced.
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class BookChapterModel(UUIDPrimaryKeyMixin, Base):
    """No ``TimestampMixin``: a chapter is written once with its book and never
    edited on its own, so per-row timestamps would all equal the book's."""

    __tablename__ = "book_chapters"
    __table_args__ = (UniqueConstraint("book_id", "index", name="uq_book_chapters_index"),)

    book_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    part_title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    block_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    words_before: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class BookBlockModel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "book_blocks"
    __table_args__ = (
        # The chapter read *is* this index: ``WHERE chapter_id = ? ORDER BY
        # position``. Unique, so a re-ingest that somehow produced two blocks
        # at one position fails loudly instead of interleaving them.
        UniqueConstraint("chapter_id", "position", name="uq_book_blocks_position"),
        # A free-text translation request is checked against this to decide
        # whether the text is a book's (cacheable) or the caller's (not).
        Index("ix_book_blocks_text_hash", "text_hash"),
    )

    chapter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("book_chapters.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="paragraph")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    words_before: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class BookProgressModel(Base):
    """One learner's place in one book. The spec's ``user_book_progress``.

    Composite primary key rather than a surrogate: there is exactly one
    position per learner per book, and the upsert is *on* that pair.
    """

    __tablename__ = "book_progress"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "book_id", name="pk_book_progress"),
        # "Continue reading": a learner's books, most recently touched first.
        Index("ix_book_progress_user_updated", "user_id", "updated_at"),
    )

    #: CASCADE from users: this is user data, and erasure must erase it.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    book_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    chapter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("book_chapters.id", ondelete="CASCADE"), nullable=False
    )
    block_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("book_blocks.id", ondelete="CASCADE"), nullable=False
    )
    char_offset: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Derived on write from word counts — never accepted from a client, for
    #: the same reason ``users.xp`` never is.
    percent: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PassageTranslationModel(Base):
    """A translated paragraph, shared by everyone who reads it.

    No ``user_id``, by the same rule as ``ai_lookup_entries``. The key includes
    ``prompt_version`` so a better translation prompt retires the old rows by
    never matching them — no purge, no migration.
    """

    __tablename__ = "passage_translations"
    __table_args__ = (
        PrimaryKeyConstraint(
            "text_hash", "target_language", "prompt_version", name="pk_passage_translations"
        ),
    )

    #: sha256 of the canonical paragraph text — ``book_blocks.text_hash``,
    #: since a paragraph that is a block is the only kind that is stored.
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Spelled as ``users.native_language`` spells it ("Persian"), not as a
    #: tag, for the reason ``lexeme_sense_translations`` gives.
    target_language: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False)
    translation: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    #: Incremented in SQL on every hit. Says which paragraphs are actually
    #: read, which decides whether pre-translating a book is worth it.
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )


class SentenceMeaningModel(Base):
    """One word's meaning in one sentence of a stored book. Impersonal, like
    ``passage_translations``, and keyed the same way plus the word."""

    __tablename__ = "sentence_meanings"
    __table_args__ = (
        PrimaryKeyConstraint(
            "sentence_hash",
            "word",
            "native_language",
            "prompt_version",
            name="pk_sentence_meanings",
        ),
    )

    sentence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The word as tapped, case-folded — "stood", not "stand": the lemma is
    #: part of the answer, not the question.
    word: Mapped[str] = mapped_column(String(120), nullable=False)
    native_language: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False)
    lemma: Mapped[str] = mapped_column(String(120), nullable=False)
    part_of_speech: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    context: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    native_meaning: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )
