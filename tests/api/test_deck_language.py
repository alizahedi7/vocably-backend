"""A pre-built deck in a language other than English.

The rule these tests pin: a Spanish or German deck is planned, built, copied and
served as that language — and nothing about an English deck changes.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports.ai_service import LearnerContext, LookupResult, MeaningSuggestion
from app.application.ports.dictionary_service import DictionaryEntry
from app.application.services.lexicon_service import LexiconService
from app.domain.enums import SenseSource
from app.infrastructure.ai.lexicon_ai_service import LexiconAIService
from app.infrastructure.db.models.deck import DeckModel
from app.infrastructure.db.models.deck_build import DeckBuildJobModel
from app.infrastructure.db.models.lexicon import LexemeModel
from app.infrastructure.db.models.word import WordModel
from app.infrastructure.db.repositories.deck_discovery_repository import (
    SqlAlchemyDeckDiscoveryRepository,
)
from app.infrastructure.db.repositories.lexicon_repository import SqlAlchemyLexiconRepository
from app.infrastructure.templates.deck_template_loader import load_template

from .conftest import UserFactory
from .test_deck_build import ScriptedProvider, StubDictionary, _service_with
from .test_discovery import create_deck, publish

SPANISH_DECK = """\
slug: basics
name: Spanish Basics
language: es
generation:
  native_language: Persian
"""

SPANISH_WORDS = """\
units:
  - name: Unit 1
    words:
      - once
      - pan
"""


@pytest.fixture
def spanish_root(tmp_path: Path) -> Path:
    base = tmp_path / "basics"
    base.mkdir()
    (base / "deck.yaml").write_text(SPANISH_DECK)
    (base / "words.yaml").write_text(SPANISH_WORDS)
    return tmp_path


async def _plan_spanish(
    session_factory: async_sessionmaker[AsyncSession],
    root: Path,
    provider: ScriptedProvider,
    owner_id: object,
) -> DeckBuildJobModel:
    async with session_factory() as session:
        job = await _service_with(session, provider, None).plan(
            load_template("basics", root=root),
            owner_id=owner_id,  # type: ignore[arg-type]
        )
        await session.commit()
    async with session_factory() as session:
        model = await session.get(DeckBuildJobModel, job.id)
        assert model is not None
        return model


async def test_planning_writes_the_language_to_the_deck_and_the_job(
    session_factory: async_sessionmaker[AsyncSession],
    spanish_root: Path,
    make_user: UserFactory,
) -> None:
    owner = await make_user(phone="+989120009101")
    job = await _plan_spanish(session_factory, spanish_root, ScriptedProvider(), owner.id)

    assert job.language == "es"
    async with session_factory() as session:
        deck = await session.get(DeckModel, job.deck_id)
        assert deck is not None
        assert deck.language == "es"


async def test_a_deck_a_learner_built_is_english(
    session_factory: async_sessionmaker[AsyncSession],
    make_user: UserFactory,
) -> None:
    owner = await make_user(phone="+989120009102")
    async with session_factory() as session:
        deck = DeckModel(user_id=owner.id, name="My words", hue=25)
        session.add(deck)
        await session.commit()
        assert deck.language == "en"


async def test_a_copy_from_explore_keeps_the_language(
    session_factory: async_sessionmaker[AsyncSession],
    spanish_root: Path,
    make_user: UserFactory,
) -> None:
    owner = await make_user(phone="+989120009103")
    learner = await make_user(phone="+989120009104")
    job = await _plan_spanish(session_factory, spanish_root, ScriptedProvider(), owner.id)

    async with session_factory() as session:
        assert job.deck_id is not None
        copy = await SqlAlchemyDeckDiscoveryRepository(session).copy_deck_to(
            job.deck_id, learner.id
        )
        await session.commit()
    assert copy.language == "es"

    async with session_factory() as session:
        stored = (
            await session.execute(select(DeckModel).where(DeckModel.id == copy.id))
        ).scalar_one()
    assert stored.language == "es"


class LanguageAwareProvider(ScriptedProvider):
    """Records the context each lookup was made in, and answers like a model would.

    Answers lowercased and without an article — what a model's ``term`` looks
    like — so the card front can be shown to come from the template instead.
    """

    def __init__(self) -> None:
        super().__init__()
        self.languages: list[str | None] = []

    async def look_up_meanings(self, term: str, learner: LearnerContext) -> LookupResult:
        self.languages.append(learner.term_language)
        result = await super().look_up_meanings(term, learner)
        return replace(result, term=term.split()[-1].lower())


async def test_a_spanish_build_is_looked_up_and_stored_as_spanish(
    session_factory: async_sessionmaker[AsyncSession],
    spanish_root: Path,
    make_user: UserFactory,
) -> None:
    """English "once" is already known; the Spanish deck must not reuse it."""
    provider = LanguageAwareProvider()
    dictionary = StubDictionary()
    dictionary.entries = {"once": DictionaryEntry(term="once", phonetic="/wʌns/")}
    async with session_factory() as session:
        await LexiconService(SqlAlchemyLexiconRepository(session), content_version=7).record(
            LookupResult(term="once", suggestions=[_sense("one time")]),
            LearnerContext(native_language="Persian"),
            source=SenseSource.LOOKUP,
        )
        await session.commit()

    owner = await make_user(phone="+989120009105")
    job = await _plan_spanish(session_factory, spanish_root, provider, owner.id)

    async with session_factory() as session:
        await _service_with(session, provider, dictionary).run_batch(job.id, limit=10)
        await session.commit()

    assert sorted(provider.calls) == ["once", "pan"], "the English lexeme must not be reused"
    assert provider.languages == ["es", "es"]
    assert dictionary.calls == [], "no English IPA for Spanish words"

    async with session_factory() as session:
        lexemes = (await session.execute(select(LexemeModel))).scalars().all()
        words = (await session.execute(select(WordModel))).scalars().all()
    assert sorted((x.language, x.lemma) for x in lexemes) == [
        ("en", "once"),
        ("es", "once"),
        ("es", "pan"),
    ]
    assert sorted(w.term for w in words) == ["once", "pan"]
    assert all(w.phonetic is None for w in words)


async def test_a_german_card_keeps_the_templates_article_and_capital(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    make_user: UserFactory,
) -> None:
    base = tmp_path / "basics"
    base.mkdir()
    (base / "deck.yaml").write_text(SPANISH_DECK.replace("language: es", "language: de"))
    (base / "words.yaml").write_text("units:\n  - name: Unit 1\n    words:\n      - der Hund\n")
    provider = LanguageAwareProvider()

    owner = await make_user(phone="+989120009106")
    job = await _plan_spanish(session_factory, tmp_path, provider, owner.id)
    async with session_factory() as session:
        outcome = await _service_with(session, provider, None).run_batch(job.id, limit=10)
        await session.commit()

    assert outcome.done == 1
    assert provider.languages == ["de"]
    async with session_factory() as session:
        word = (await session.execute(select(WordModel))).scalar_one()
        lexeme = (await session.execute(select(LexemeModel))).scalar_one()
    assert word.term == "der Hund"
    assert (lexeme.language, lexeme.lemma) == ("de", "hund")


async def test_an_english_build_still_asks_without_a_language(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    make_user: UserFactory,
) -> None:
    base = tmp_path / "basics"
    base.mkdir()
    (base / "deck.yaml").write_text(SPANISH_DECK.replace("language: es\n", ""))
    (base / "words.yaml").write_text(SPANISH_WORDS)
    provider = LanguageAwareProvider()

    owner = await make_user(phone="+989120009107")
    job = await _plan_spanish(session_factory, tmp_path, provider, owner.id)
    async with session_factory() as session:
        await _service_with(session, provider, None).run_batch(job.id, limit=10)
        await session.commit()

    assert job.language == "en"
    assert provider.languages == [None, None]


def _sense(definition: str) -> MeaningSuggestion:
    return MeaningSuggestion(
        native_meaning="یک بار",
        definition=definition,
        example="I went there once.",
        context="Frequency",
        part_of_speech="adverb",
    )


async def test_the_lexicon_layer_keeps_a_spanish_word_apart_from_its_english_twin(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """English "once" is known; Spanish "once" (eleven) must still be bought, once."""
    provider = ScriptedProvider()
    async with session_factory() as session:
        lexicon = LexiconService(SqlAlchemyLexiconRepository(session), content_version=7)
        await lexicon.record(
            await provider.look_up_meanings("once", LearnerContext(native_language="Persian")),
            LearnerContext(native_language="Persian"),
            source=SenseSource.LOOKUP,
        )
        provider.calls.clear()

        chain = LexiconAIService(provider, lexicon)
        spanish = LearnerContext(native_language="Persian", term_language="es")
        await chain.look_up_meanings("once", spanish)
        await chain.look_up_meanings("once", spanish)
        await session.commit()

    assert provider.calls == ["once"], "bought once for Spanish, then served from the lexicon"
    async with session_factory() as session:
        rows = (await session.execute(select(LexemeModel))).scalars().all()
    assert sorted((r.language, r.lemma) for r in rows) == [("en", "once"), ("es", "once")]


async def test_the_api_reports_each_decks_language(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Additive everywhere a deck is described: the list, Explore and the preview."""
    english = await create_deck(client, auth_headers, "Mine")
    spanish = await create_deck(client, auth_headers, "Spanish Basics")
    async with session_factory() as session:
        await session.execute(
            update(DeckModel).where(DeckModel.id == UUID(spanish)).values(language="es")
        )
        await session.commit()
    await publish(session_factory, spanish)

    created = await client.post(
        "/api/v1/decks", headers=auth_headers, json={"name": "Another", "hue": 1}
    )
    assert created.json()["language"] == "en"

    listed = await client.get("/api/v1/decks", headers=auth_headers)
    languages = {d["id"]: d["language"] for d in listed.json()}
    assert languages[english] == "en"
    assert languages[spanish] == "es"

    explore = await client.get("/api/v1/decks/public", headers=auth_headers)
    assert [d["language"] for d in explore.json()["decks"]] == ["es"]

    detail = await client.get(f"/api/v1/decks/public/{spanish}", headers=auth_headers)
    assert detail.json()["language"] == "es"
