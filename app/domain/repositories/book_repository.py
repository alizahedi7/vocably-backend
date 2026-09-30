"""Ports: persistence for books, reading positions and passage translations.

Three ports in one module because they are one feature, but three ports rather
than one because they have three different contracts:

* :class:`BookRepository` writes are **whole-book and idempotent** — a book is
  ingested in one transaction or not at all, and ingesting it again is a
  content-hash comparison, never a second copy.
* :class:`BookProgressRepository` is the only one that sees a ``user_id``.
* :class:`PassageTranslationRepository` is a **best-effort cache**: every
  implementation may fail, and the caller serves the translation anyway.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from uuid import UUID

from app.domain.entities.book import (
    Book,
    BookBlock,
    BookChapter,
    PassageTranslation,
    ReadingPosition,
)


class BookRepository(ABC):
    @abstractmethod
    async def list_public(
        self, *, language: str | None = None, limit: int = 20, offset: int = 0
    ) -> tuple[list[Book], int]:
        """The library, newest publication first, without chapters loaded."""

    @abstractmethod
    async def list_all(self, *, limit: int = 25, offset: int = 0) -> tuple[list[Book], int]:
        """Every book, published or not, most recently ingested first. Admin only."""

    @abstractmethod
    async def get(self, book_id: UUID) -> Book | None:
        """The book with its chapter list (no blocks), public or not.

        Visibility is the *service's* decision: an admin previews an
        unpublished book through the same read a learner uses on a public one.
        """

    @abstractmethod
    async def get_by_source(self, source: str, source_id: str) -> Book | None: ...

    @abstractmethod
    async def get_chapter(
        self, book_id: UUID, chapter_id: UUID, *, after: int = -1, limit: int = 400
    ) -> BookChapter | None:
        """One chapter with its blocks after position ``after``, in order.

        ``book_id`` is a predicate, not a convenience: a chapter id from a
        different book must answer ``None``, or a URL could read chapter text
        out of a book that is not public.
        """

    @abstractmethod
    async def get_block(self, block_id: UUID) -> tuple[Book, BookChapter, BookBlock] | None:
        """A block with its chapter and book, for lookups and progress writes."""

    @abstractmethod
    async def get_blocks_by_hash(self, text_hash: str) -> Sequence[BookBlock]:
        """Blocks holding exactly this text. Empty means the text is not a
        book's, which decides whether a translation of it may be stored."""

    @abstractmethod
    async def create(self, book: Book) -> Book:
        """Insert a book with **every** chapter and block, in one transaction.

        Raises :class:`~app.core.exceptions.AlreadyExistsError` on the source
        unique constraint; the service checks first, and this is the race's
        backstop.
        """

    @abstractmethod
    async def replace_content(self, book: Book) -> Book:
        """Swap an **unpublished** book's chapters and blocks for new ones.

        Never called on a public book: reading positions point at block ids,
        and a published book's text is a promise to everyone holding one.
        """

    @abstractmethod
    async def set_published(self, book_id: UUID, is_public: bool) -> Book | None:
        """Idempotent, both directions, like ``PATCH /admin/decks/{id}/publish``."""

    @abstractmethod
    async def set_rights(self, book_id: UUID, rights: str) -> Book | None:
        """Record the licence statement an operator supplies for an upload."""


class BookProgressRepository(ABC):
    @abstractmethod
    async def get(self, user_id: UUID, book_id: UUID) -> ReadingPosition | None: ...

    @abstractmethod
    async def list_for_user(self, user_id: UUID, *, limit: int = 20) -> list[ReadingPosition]:
        """Most recently touched first — the "Continue reading" shelf."""

    @abstractmethod
    async def upsert(self, position: ReadingPosition) -> ReadingPosition:
        """``INSERT … ON CONFLICT (user_id, book_id) DO UPDATE``.

        Last writer wins, and that is the product rule: a learner may go back,
        so there is no "forward only" guard. Two devices syncing in the same
        second land one row either way, which is what the conflict clause is
        for.
        """

    @abstractmethod
    async def delete(self, user_id: UUID, book_id: UUID) -> bool:
        """Take a book off the shelf. Nothing else is deleted."""


class PassageTranslationRepository(ABC):
    @abstractmethod
    async def get(
        self, text_hash: str, *, target_language: str, prompt_version: int
    ) -> PassageTranslation | None:
        """The cached translation, bumping ``hit_count`` in SQL on the way."""

    @abstractmethod
    async def put(self, translation: PassageTranslation) -> None:
        """``ON CONFLICT DO NOTHING``: the first translation stored wins, so a
        race between two readers of one paragraph cannot flap the text."""
