"""The reader service's paid paths, with the model replaced by fakes.

The endpoint tests run on the stub; these pin what only a model changes. The
meaning of a word in a sentence is asked *alongside* a cold lookup, not after
it; it is not asked at all for a warm word the sentence decides; it is
memoised, and kept durably for a book's sentence; it is matched to a stored
sense when one fits and shown as itself when none does; an outage degrades to
the most common sense instead of an error; a wrong lemma gets one retry; and
the free-text log carries a fingerprint, never the text.
"""

from __future__ import annotations

import asyncio
import logging
import time

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports.ai_service import (
    AIService,
    GeneratedStory,
    LearnerContext,
    LookupResult,
    MeaningSuggestion,
)
from app.application.ports.reader_ai import ContextualMeaning, PassageTranslationResult
from app.application.services.book_ingest_service import materialise
from app.application.services.reader_service import ReaderService
from app.core.exceptions import ExternalServiceError
from app.domain.entities.book import Book
from app.domain.entities.user import User
from app.domain.enums import BookSource
from app.domain.services.contextual_sense import ContextualSelection
from app.infrastructure.books.epub import parse_epub
from app.infrastructure.books.sources import FetchedBook
from app.infrastructure.db.repositories.book_repository import (
    SqlAlchemyBookProgressRepository,
    SqlAlchemyBookRepository,
    SqlAlchemyPassageTranslationRepository,
    SqlAlchemySentenceMeaningRepository,
)
from app.infrastructure.nlp.simplemma_lemmatizer import SimplemmaLemmatizer
from tests.epub_builder import Book as EpubBook
from tests.epub_builder import Doc, build_epub, prose

Sessions = async_sessionmaker[AsyncSession]

AMBIGUOUS = "She walked slowly towards the bank without saying anything."
BANK = [
    MeaningSuggestion(
        "بانک", "an organization that keeps and lends money", "I paid it in.", "Finance", "noun"
    ),
    MeaningSuggestion("ساحل", "the land along the side of a river", "", "River", "noun"),
]
RIVER_MEANING = ContextualMeaning(
    "bank", "noun", "Geography", "the ground along the edge of a river", "ساحل", "fake", "m"
)
AVIATION_MEANING = ContextualMeaning(
    "bank", "verb", "Aviation", "to tilt an aircraft sideways while turning", "کج شدن", "fake", "m"
)


class Lookups(AIService):
    def __init__(self, known: dict[str, list[MeaningSuggestion]], *, delay: float = 0.0) -> None:
        self.known = known
        self.delay = delay
        self.terms: list[str] = []
        self.finished_at: float | None = None

    async def look_up_meanings(self, term: str, learner: LearnerContext) -> LookupResult:
        self.terms.append(term)
        if self.delay:
            await asyncio.sleep(self.delay)
        self.finished_at = time.monotonic()
        return LookupResult(term=term, suggestions=list(self.known.get(term, [])))

    async def generate_story(self, words: list[str], learner: LearnerContext) -> GeneratedStory:
        return GeneratedStory(text="", words_used=words)


class Model:
    def __init__(self, meaning: ContextualMeaning = RIVER_MEANING, *, down: bool = False) -> None:
        self.meaning, self.down = meaning, down
        self.asked: list[tuple[str, str]] = []
        self.asked_at: float | None = None

    async def meaning_in_context(
        self, word: str, sentence: str, learner: LearnerContext
    ) -> ContextualMeaning:
        self.asked.append((word, sentence))
        self.asked_at = time.monotonic()
        if self.down:
            raise ExternalServiceError("every gateway is down")
        return self.meaning

    async def translate_passage(
        self, text: str, target_language: str, preceding: str = "", book_title: str = ""
    ) -> PassageTranslationResult:
        return PassageTranslationResult(f"[{target_language}] {text}")


class Memo:
    def __init__(self) -> None:
        self.saved: dict[str, ContextualMeaning] = {}

    def meaning_key(self, word: str, sentence: str, native_language: str, version: int) -> str:
        return f"{version}:{word}:{sentence}:{native_language}"

    async def get_meaning(self, key: str) -> ContextualMeaning | None:
        return self.saved.get(key)

    async def put_meaning(self, key: str, meaning: ContextualMeaning) -> None:
        self.saved[key] = meaning


def reader(
    session: AsyncSession, lookups: Lookups, model: Model, memo: Memo | None = None
) -> ReaderService:
    return ReaderService(
        books=SqlAlchemyBookRepository(session),
        progress=SqlAlchemyBookProgressRepository(session),
        translations=SqlAlchemyPassageTranslationRepository(session),
        meanings=SqlAlchemySentenceMeaningRepository(session),
        ai=lookups,
        explainer=model,
        translator=model,
        lemmatizer=SimplemmaLemmatizer(),
        memo=memo,
        prompt_version=2,
        reader_prompt_version=2,
    )


LEARNER = User(native_language="Persian")


async def test_a_cold_word_is_explained_while_its_lookup_is_still_running(
    session_factory: Sessions,
) -> None:
    lookups, model = Lookups({"bank": BANK}, delay=0.4), Model()
    async with session_factory() as session:
        started = time.monotonic()
        view = await reader(session, lookups, model).look_up(
            LEARNER, word="bank", sentence=AMBIGUOUS
        )
        elapsed = time.monotonic() - started
    assert model.asked_at is not None and lookups.finished_at is not None
    assert model.asked_at < lookups.finished_at  # asked before the lookup answered
    assert elapsed < 0.4 + 0.3  # the two ran side by side, not one after the other
    assert (view.contextual_index, view.selection) == (1, ContextualSelection.MATCHED)
    assert view.meaning is not None and view.meaning.context == "River"


async def test_a_warm_word_the_sentence_decides_costs_no_call(session_factory: Sessions) -> None:
    lookups, model = Lookups({"bank": BANK, "river": [BANK[1]]}), Model()
    async with session_factory() as session:
        service = reader(session, lookups, model)
        decided = await service.look_up(
            LEARNER, word="bank", sentence="She paid her wages into the bank to keep the money."
        )
        single = await service.look_up(LEARNER, word="river", sentence=AMBIGUOUS)
    assert (decided.contextual_index, decided.selection) == (0, ContextualSelection.OVERLAP)
    assert (single.contextual_index, single.selection) == (0, ContextualSelection.ONLY)
    assert model.asked == []


async def test_a_meaning_the_lexicon_lacks_is_shown_as_itself(session_factory: Sessions) -> None:
    model = Model(AVIATION_MEANING)
    async with session_factory() as session:
        view = await reader(session, Lookups({"bank": BANK}), model).look_up(
            LEARNER, word="bank", sentence="The pilot had to bank the plane sharply."
        )
    assert view.selection is ContextualSelection.CONTEXTUAL
    assert view.contextual_index is None
    assert view.meaning is not None
    assert view.meaning.definition == AVIATION_MEANING.definition
    assert view.meaning.example == "The pilot had to bank the plane sharply."
    # The lexicon is untouched: the deck of senses is still the two it held.
    assert len(view.lookup.result.suggestions) == 2


async def test_a_meaning_is_asked_once_per_sentence(session_factory: Sessions) -> None:
    model, memo = Model(), Memo()
    async with session_factory() as session:
        service = reader(session, Lookups({"bank": BANK}), model, memo)
        first = await service.look_up(LEARNER, word="bank", sentence=AMBIGUOUS)
        again = await service.look_up(LEARNER, word="bank", sentence=AMBIGUOUS)
    assert first.contextual_index == again.contextual_index == 1
    assert len(model.asked) == 1


async def seed_book(sessions: Sessions) -> Book:
    body = f"<h2>I</h2><p>{AMBIGUOUS} {prose(4)}</p>"
    book = materialise(
        parse_epub(build_epub(EpubBook(docs=[Doc("c1.xhtml", body, "chapter")]))),
        FetchedBook(
            source=BookSource.GUTENBERG, source_id="11", source_url="", data=b"", rights="PD"
        ),
    )
    async with sessions() as session:
        repo = SqlAlchemyBookRepository(session)
        stored = await repo.create(book)
        stored = await repo.set_published(stored.id, True) or stored
        await session.commit()
    return stored


async def test_a_books_sentence_keeps_its_meaning_durably(session_factory: Sessions) -> None:
    book = await seed_book(session_factory)
    async with session_factory() as session:
        chapter = await SqlAlchemyBookRepository(session).get_chapter(book.id, book.chapters[0].id)
    assert chapter is not None
    block = chapter.blocks[0]
    start = block.text.index("bank")
    model = Model()
    async with session_factory() as session:
        view = await reader(session, Lookups({"bank": BANK}), model).look_up(
            LEARNER, word="bank", block_id=block.id, char_start=start, char_end=start + 4
        )
        await session.commit()
    assert view.selection is ContextualSelection.MATCHED
    # A new session, no memo, a different fake: the answer is read back from
    # the table and the model is not asked again.
    later = Model(AVIATION_MEANING)
    async with session_factory() as session:
        again = await reader(session, Lookups({"bank": BANK}), later).look_up(
            LEARNER, word="bank", block_id=block.id, char_start=start, char_end=start + 4
        )
    assert later.asked == []
    assert again.contextual_index == 1


async def test_an_outage_degrades_to_the_most_common_sense(session_factory: Sessions) -> None:
    memo = Memo()
    async with session_factory() as session:
        view = await reader(session, Lookups({"bank": BANK}), Model(down=True), memo).look_up(
            LEARNER, word="bank", sentence=AMBIGUOUS
        )
    assert (view.contextual_index, view.selection) == (0, ContextualSelection.FIRST)
    assert memo.saved == {}  # an outage is not an answer, and is not remembered


async def test_a_wrong_lemma_gets_one_retry_with_what_was_tapped(
    session_factory: Sessions,
) -> None:
    # simplemma reads "saw" as "see"; this book meant the tool.
    lookups = Lookups({"saw": [BANK[0]]})
    async with session_factory() as session:
        view = await reader(session, lookups, Model()).look_up(LEARNER, word="saw")
    assert lookups.terms == ["see", "saw"]
    assert view.lemma == "see" and view.lookup.result.term == "saw"


async def test_the_free_text_log_carries_a_fingerprint_and_never_the_text(
    session_factory: Sessions, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "My neighbour's name is Parisa and she lives at number 12."
    caplog.set_level(logging.INFO, logger="vocably.reader")
    async with session_factory() as session:
        await reader(session, Lookups({}), Model()).translate(LEARNER, text=secret)
    lines = [r.getMessage() for r in caplog.records if "free-text translation" in r.getMessage()]
    assert len(lines) == 1
    assert "fp=" in lines[0] and "is_book_text=False" in lines[0]
    assert "Parisa" not in lines[0] and "neighbour" not in lines[0]
