"""Pre-warming a published book: every word it contains, looked up once.

The reader's slow case is a word nobody has looked up: the lookup itself is a
model call, and nothing the reader does can make that faster. What it can do
is pay for it *before* a learner taps. A published book is a fixed, finite
vocabulary — a few thousand lemmas for a novel — so each is sent through the
same lookup chain a tap uses, and after that every tap in the book finds its
senses in the lexicon and waits only for the meaning-in-context, if even that.

The list of lemmas is a deterministic function of the book, sorted, so a
worker that dies at lemma 1,200 of 6,000 is re-run from 1,200 and a redelivered
message repeats nothing: a lemma already in the cache or the lexicon costs an
indexed read, which is what makes the whole thing idempotent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from app.application.ports.lemmatizer import Lemmatizer
from app.application.ports.lookup_cache import normalize_lookup_input
from app.core.exceptions import NotFoundError
from app.domain.repositories.book_repository import BookRepository

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
#: A one-letter token is an initial or a typo, and a lookup for it is noise.
_MIN_CHARS = 2


@dataclass(frozen=True, slots=True)
class WarmPlan:
    """Which lemmas a run covers, and whether another run follows."""

    lemmas: list[str]
    total: int
    offset: int

    @property
    def next_offset(self) -> int | None:
        end = self.offset + len(self.lemmas)
        return end if end < self.total else None


class BookWarmService:
    def __init__(self, books: BookRepository, lemmatizer: Lemmatizer) -> None:
        self._books = books
        self._lemmatizer = lemmatizer

    async def lemmas_of(self, book_id: UUID) -> list[str]:
        """Every distinct lemma in the book, sorted. Deterministic by design."""
        book = await self._books.get(book_id)
        if book is None:
            raise NotFoundError("Book not found.")
        seen: set[str] = set()
        for summary in book.chapters:
            chapter = await self._books.get_chapter(book.id, summary.id, limit=100_000)
            if chapter is None:
                continue
            for block in chapter.blocks:
                for token in _WORD.findall(block.text):
                    surface = normalize_lookup_input(token)
                    if len(surface) < _MIN_CHARS:
                        continue
                    lemma = self._lemmatizer.lemma(surface, language=book.language) or surface
                    seen.add(lemma)
        return sorted(seen)

    async def plan(self, book_id: UUID, *, offset: int, batch: int) -> WarmPlan:
        lemmas = await self.lemmas_of(book_id)
        return WarmPlan(lemmas=lemmas[offset : offset + batch], total=len(lemmas), offset=offset)
