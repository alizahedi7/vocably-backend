"""Pre-warming a book: the vocabulary list is the book's, sorted, and sliced.

Deterministic on purpose: a re-run from any offset covers exactly the lemmas
the dead run did not, and a redelivered message repeats no work.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.services.book_ingest_service import materialise
from app.application.services.book_warm_service import BookWarmService, WarmPlan
from app.core.exceptions import NotFoundError
from app.domain.enums import BookSource
from app.infrastructure.books.epub import parse_epub
from app.infrastructure.books.sources import FetchedBook
from app.infrastructure.db.repositories.book_repository import SqlAlchemyBookRepository
from app.infrastructure.nlp.simplemma_lemmatizer import SimplemmaLemmatizer
from tests.epub_builder import Book as EpubBook
from tests.epub_builder import Doc, build_epub

Sessions = async_sessionmaker[AsyncSession]


async def test_the_vocabulary_is_lemmatised_deduplicated_and_sorted(
    session_factory: Sessions,
) -> None:
    body = (
        "<h2>I</h2><p>The children stood and stood. Banks, banks, a bank! "
        + "Once upon a time there were forty words in this chapter of the book. " * 3
        + "</p>"
    )
    epub = EpubBook(
        docs=[
            Doc("c1.xhtml", body, "chapter"),
            Doc(
                "c2.xhtml",
                "<h2>II</h2><p>"
                + "The last chapter was even longer than the first one, or so it seemed. " * 3
                + "</p>",
                "chapter",
            ),
        ]
    )
    book = materialise(
        parse_epub(build_epub(epub)),
        FetchedBook(source=BookSource.UPLOAD, source_id="w", source_url="", data=b""),
    )
    async with session_factory() as session:
        stored = await SqlAlchemyBookRepository(session).create(book)
        await session.commit()
    async with session_factory() as session:
        service = BookWarmService(SqlAlchemyBookRepository(session), SimplemmaLemmatizer())
        lemmas = await service.lemmas_of(stored.id)
        plan = await service.plan(stored.id, offset=0, batch=5)
        with pytest.raises(NotFoundError):
            await service.lemmas_of(uuid4())
    assert lemmas == sorted(set(lemmas))
    assert "child" in lemmas and "children" not in lemmas
    assert "stand" in lemmas and "stood" not in lemmas
    assert "bank" in lemmas and "banks" not in lemmas
    # One-letter tokens ("a", "I") are noise, not vocabulary.
    assert "a" not in lemmas and "i" not in lemmas
    assert plan == WarmPlan(lemmas=lemmas[:5], total=len(lemmas), offset=0)
    assert plan.next_offset == 5


def test_the_last_slice_has_no_next() -> None:
    assert WarmPlan(lemmas=["x", "y"], total=7, offset=5).next_offset is None
    assert WarmPlan(lemmas=["x"], total=7, offset=5).next_offset == 6
    assert WarmPlan(lemmas=[], total=0, offset=0).next_offset is None
