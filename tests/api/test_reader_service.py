"""The reader service's paid paths, with the model replaced by fakes.

The endpoint tests run on the stub, which always picks the first sense; these
pin what only a model changes. A disambiguation is asked once per sentence and
memoised; an unsure or out-of-range answer means "none"; an outage degrades to
the most common sense instead of an error; a wrong lemma gets one retry with
what was tapped; and the free-text log carries a fingerprint, never the text.
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports.ai_service import (
    AIService,
    GeneratedStory,
    LearnerContext,
    LookupResult,
    MeaningSuggestion,
)
from app.application.ports.reader_ai import Disambiguation, PassageTranslationResult
from app.application.services.reader_service import ReaderService
from app.core.exceptions import ExternalServiceError
from app.domain.entities.user import User
from app.domain.services.contextual_sense import ContextualSelection
from app.infrastructure.db.repositories.book_repository import (
    SqlAlchemyBookProgressRepository,
    SqlAlchemyBookRepository,
    SqlAlchemyPassageTranslationRepository,
)
from app.infrastructure.nlp.simplemma_lemmatizer import SimplemmaLemmatizer

Sessions = async_sessionmaker[AsyncSession]

AMBIGUOUS = "She walked slowly towards the bank without saying anything."
BANK = [
    MeaningSuggestion("بانک", "an organization that keeps money", "", "Finance", "noun"),
    MeaningSuggestion("ساحل", "the land along the side of a river", "", "River", "noun"),
]


class Lookups(AIService):
    def __init__(self, known: dict[str, list[MeaningSuggestion]]) -> None:
        self.known = known
        self.terms: list[str] = []

    async def look_up_meanings(self, term: str, learner: LearnerContext) -> LookupResult:
        self.terms.append(term)
        return LookupResult(term=term, suggestions=list(self.known.get(term, [])))

    async def generate_story(self, words: list[str], learner: LearnerContext) -> GeneratedStory:
        return GeneratedStory(text="", words_used=words)


class Model:
    def __init__(self, *, index: int = 1, confidence: float = 0.9, down: bool = False) -> None:
        self.index, self.confidence, self.down = index, confidence, down
        self.asked = 0

    async def disambiguate_sense(
        self, term: str, sentence: str, senses: list[MeaningSuggestion], learner: LearnerContext
    ) -> Disambiguation:
        self.asked += 1
        if self.down:
            raise ExternalServiceError("every gateway is down")
        return Disambiguation(index=self.index, confidence=self.confidence)

    async def translate_passage(
        self, text: str, target_language: str, preceding: str = "", book_title: str = ""
    ) -> PassageTranslationResult:
        return PassageTranslationResult(f"[{target_language}] {text}")


class Memo:
    def __init__(self) -> None:
        self.saved: dict[str, int] = {}

    def disambiguation_key(self, lookup_id: str, sentence: str, prompt_version: int) -> str:
        return f"{prompt_version}:{lookup_id}:{sentence}"

    async def get_disambiguation(self, key: str) -> int | None:
        return self.saved.get(key)

    async def put_disambiguation(self, key: str, index: int) -> None:
        self.saved[key] = index


def reader(
    session: AsyncSession,
    lookups: Lookups,
    model: Model,
    memo: Memo | None = None,
) -> ReaderService:
    return ReaderService(
        books=SqlAlchemyBookRepository(session),
        progress=SqlAlchemyBookProgressRepository(session),
        translations=SqlAlchemyPassageTranslationRepository(session),
        ai=lookups,
        disambiguator=model,
        translator=model,
        lemmatizer=SimplemmaLemmatizer(),
        memo=memo,
        prompt_version=2,
        reader_prompt_version=1,
    )


LEARNER = User(native_language="Persian")


async def test_an_ambiguous_sentence_asks_the_model_once(session_factory: Sessions) -> None:
    model, memo = Model(index=1), Memo()
    async with session_factory() as session:
        service = reader(session, Lookups({"bank": BANK}), model, memo)
        first = await service.look_up(LEARNER, word="bank", sentence=AMBIGUOUS)
        again = await service.look_up(LEARNER, word="bank", sentence=AMBIGUOUS)
    assert (first.contextual_index, first.selection) == (1, ContextualSelection.MODEL)
    assert (again.contextual_index, again.selection) == (1, ContextualSelection.MODEL)
    assert model.asked == 1


async def test_an_unsure_model_is_recorded_as_no_fitting_sense(
    session_factory: Sessions,
) -> None:
    model, memo = Model(index=1, confidence=0.2), Memo()
    async with session_factory() as session:
        service = reader(session, Lookups({"bank": BANK}), model, memo)
        view = await service.look_up(LEARNER, word="bank", sentence=AMBIGUOUS)
        again = await service.look_up(LEARNER, word="bank", sentence=AMBIGUOUS)
    assert (view.contextual_index, view.selection) == (None, ContextualSelection.NONE)
    # "None of these" is memoised too: it is an answer, and it was paid for.
    assert again.contextual_index is None and model.asked == 1


@pytest.mark.parametrize("index", [-1, 2, 9])
async def test_an_index_outside_the_list_means_none(session_factory: Sessions, index: int) -> None:
    async with session_factory() as session:
        view = await reader(session, Lookups({"bank": BANK}), Model(index=index)).look_up(
            LEARNER, word="bank", sentence=AMBIGUOUS
        )
    assert view.contextual_index is None


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
