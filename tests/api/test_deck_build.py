"""The deck build pipeline, end to end against a real database.

The properties under test are the ones that only show up when the whole thing
runs: that a word already in the lexicon costs nothing, that a crashed worker's
item comes back, that one dead word does not stop 500 others, and that a
half-built deck cannot reach Explore.

The provider here counts its calls, so "costs nothing" is an assertion rather
than a hope.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports.ai_service import (
    AIService,
    GeneratedStory,
    LearnerContext,
    LookupResult,
    MeaningSuggestion,
)
from app.application.ports.dictionary_service import DictionaryEntry
from app.application.services.deck_build_service import (
    PROVIDER_FAILURE_STREAK,
    DeckBuildService,
)
from app.application.services.lexicon_service import LexiconService
from app.core.exceptions import ExternalServiceError, NotFoundError
from app.domain.enums import DeckBuildItemState, DeckBuildState, SenseSelection
from app.infrastructure.db.models.deck import DeckModel
from app.infrastructure.db.models.deck_build import DeckBuildItemModel
from app.infrastructure.db.models.deck_unit import DeckUnitModel
from app.infrastructure.db.models.lexicon import LexemeModel, LexemeSenseModel
from app.infrastructure.db.models.word import WordModel
from app.infrastructure.db.repositories.deck_build_repository import (
    SqlAlchemyDeckBuildRepository,
)
from app.infrastructure.db.repositories.deck_discovery_repository import (
    SqlAlchemyDeckDiscoveryRepository,
)
from app.infrastructure.db.repositories.deck_member_repository import (
    SqlAlchemyDeckMemberRepository,
)
from app.infrastructure.db.repositories.deck_repository import SqlAlchemyDeckRepository
from app.infrastructure.db.repositories.deck_unit_repository import SqlAlchemyDeckUnitRepository
from app.infrastructure.db.repositories.lexicon_repository import SqlAlchemyLexiconRepository
from app.infrastructure.db.repositories.word_repository import SqlAlchemyWordRepository
from app.infrastructure.templates.deck_template_loader import load_template

from .conftest import UserFactory

CONTENT_VERSION = 7

DECK_YAML = """\
slug: tiny
name: Tiny Deck
category: business
description: A short deck.
description_fa: یک دستهٔ کوچک.
icon: tiny
generation:
  native_language: Persian
  sense_selection: [explicit, hint, category, first]
  enrichment: allowed
structure:
  expected_units: 2
  expected_words: 3
"""

WORDS_YAML = """\
units:
  - name: Module 1
    words:
      - term: run
        sense: {pos: verb, context: Management}
      - keen
  - name: Module 2
    words:
      - tact
"""


class ScriptedProvider(AIService):
    """A provider whose answers and failures are set per term by the test."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.enrich_calls: list[tuple[str, str]] = []
        self.fail_for: set[str] = set()
        self.senses: dict[str, list[MeaningSuggestion]] = {}
        self.enrichment: list[MeaningSuggestion] = []
        #: Raised for every term — stands in for a provider that is down.
        self.fail_with: Exception | None = None

    async def look_up_meanings(self, term: str, learner: LearnerContext) -> LookupResult:
        self.calls.append(term)
        if self.fail_with is not None:
            raise self.fail_with
        if term in self.fail_for:
            raise RuntimeError(f"provider is down for {term}")
        senses = self.senses.get(term) or [
            MeaningSuggestion(
                native_meaning=f"معنی {term}",
                definition=f"the ordinary meaning of {term}",
                example=f"They use {term} every day.",
                context="General",
                part_of_speech="noun",
            )
        ]
        return LookupResult(term=term, suggestions=senses, phonetic="")

    async def generate_story(self, words: list[str], learner: LearnerContext) -> GeneratedStory:
        return GeneratedStory(text="", words_used=[])

    async def enrich_senses(
        self,
        term: str,
        known: list[MeaningSuggestion],
        wanted: str,
        learner: LearnerContext,
        max_new: int,
    ) -> list[MeaningSuggestion]:
        self.enrich_calls.append((term, wanted))
        return self.enrichment[:max_new]


@pytest.fixture
def provider() -> ScriptedProvider:
    return ScriptedProvider()


@pytest.fixture
def template_root(tmp_path: Path) -> Path:
    base = tmp_path / "tiny"
    base.mkdir()
    (base / "deck.yaml").write_text(DECK_YAML)
    (base / "words.yaml").write_text(WORDS_YAML)
    return tmp_path


@pytest.fixture
def build_service(
    session_factory: async_sessionmaker[AsyncSession], provider: ScriptedProvider
) -> AsyncGenerator[object, None]:
    """A factory that builds the service against a fresh session per call.

    Per call, not per test, because the pipeline's whole transaction discipline
    is that claims commit separately from results — a service pinned to one
    long-lived session would test something the worker never does.
    """

    def _make(session: AsyncSession) -> DeckBuildService:
        lexicon = LexiconService(
            SqlAlchemyLexiconRepository(session),
            content_version=CONTENT_VERSION,
            provider="stub",
        )
        return DeckBuildService(
            SqlAlchemyDeckBuildRepository(session),
            SqlAlchemyDeckRepository(session),
            SqlAlchemyDeckMemberRepository(session),
            SqlAlchemyDeckUnitRepository(session),
            SqlAlchemyWordRepository(session),
            SqlAlchemyDeckDiscoveryRepository(session),
            lexicon,
            provider,
            content_version=CONTENT_VERSION,
            enricher=provider,
        )

    yield _make


async def _plan(
    make, session_factory: async_sessionmaker[AsyncSession], root: Path, owner_id
) -> object:
    template = load_template("tiny", root=root)
    async with session_factory() as session:
        job = await make(session).plan(template, owner_id=owner_id)
        await session.commit()
    return job


async def _run(make, session_factory: async_sessionmaker[AsyncSession], job_id, limit=10):
    async with session_factory() as session:
        outcome = await make(session).run_batch(job_id, limit=limit)
        await session.commit()
    return outcome


async def test_planning_writes_the_deck_units_and_plan_without_spending(
    build_service,
    session_factory: async_sessionmaker[AsyncSession],
    template_root: Path,
    provider: ScriptedProvider,
    make_user: UserFactory,
) -> None:
    owner = await make_user(phone="+989120009001")
    job = await _plan(build_service, session_factory, template_root, owner.id)

    assert job.items_total == 3
    assert job.state is DeckBuildState.PLANNED
    assert provider.calls == []

    async with session_factory() as session:
        deck = (await session.execute(select(DeckModel))).scalars().one()
        units = (await session.execute(select(DeckUnitModel))).scalars().all()
        items = (
            (
                await session.execute(
                    select(DeckBuildItemModel).order_by(DeckBuildItemModel.position)
                )
            )
            .scalars()
            .all()
        )

    # Private, and nothing in the pipeline will ever change that.
    assert deck.is_public is False
    assert deck.published_at is None
    assert deck.category == "business"
    assert [u.name for u in units] == ["Module 1", "Module 2"]
    # The book's order, not a hash's.
    assert [i.source_term for i in items] == ["run", "keen", "tact"]


async def test_a_build_writes_one_card_per_word_in_template_order(
    build_service,
    session_factory: async_sessionmaker[AsyncSession],
    template_root: Path,
    make_user: UserFactory,
) -> None:
    owner = await make_user(phone="+989120009002")
    job = await _plan(build_service, session_factory, template_root, owner.id)

    outcome = await _run(build_service, session_factory, job.id)

    assert outcome.done == 3
    assert outcome.has_more is False
    assert outcome.finished_state is DeckBuildState.COMPLETED

    async with session_factory() as session:
        words = (
            (await session.execute(select(WordModel).order_by(WordModel.created_at)))
            .scalars()
            .all()
        )
    assert [w.term for w in words] == ["run", "keen", "tact"]
    assert all(w.lexeme_sense_id is not None for w in words)
    assert all(w.meaning for w in words), "every card must carry a native headline"
    assert all(w.unit_id is not None for w in words)


async def test_a_word_already_in_the_lexicon_costs_no_provider_call(
    build_service,
    session_factory: async_sessionmaker[AsyncSession],
    template_root: Path,
    provider: ScriptedProvider,
    make_user: UserFactory,
) -> None:
    """The reason the pipeline exists. Reuse is counted, not assumed."""
    owner = await make_user(phone="+989120009003")

    # Someone looked "keen" up last month.
    async with session_factory() as session:
        lexicon = LexiconService(
            SqlAlchemyLexiconRepository(session), content_version=CONTENT_VERSION
        )
        await lexicon.record(
            LookupResult(
                term="keen",
                suggestions=[
                    MeaningSuggestion(
                        native_meaning="مشتاق",
                        definition="wanting to do something very much",
                        example="She is keen to help.",
                        context="Enthusiasm",
                        part_of_speech="adjective",
                    )
                ],
            ),
            LearnerContext(native_language="Persian"),
            source=__import__("app.domain.enums", fromlist=["SenseSource"]).SenseSource.LOOKUP,
        )
        await session.commit()

    job = await _plan(build_service, session_factory, template_root, owner.id)
    await _run(build_service, session_factory, job.id)

    assert "keen" not in provider.calls
    assert sorted(provider.calls) == ["run", "tact"]

    async with session_factory() as session:
        refreshed = await SqlAlchemyDeckBuildRepository(session).get_job(job.id)
    assert refreshed is not None
    assert refreshed.lexemes_reused == 1
    assert refreshed.lexemes_generated == 2
    # Two lookups plus one enrichment: "run" is pinned to a sense the generic
    # stub answer does not contain, which is the third resolution path.
    assert refreshed.ai_calls == 3
    assert [term for term, _ in provider.enrich_calls] == ["run"]


async def test_a_pinned_sense_that_is_missing_triggers_exactly_one_enrichment(
    build_service,
    session_factory: async_sessionmaker[AsyncSession],
    template_root: Path,
    provider: ScriptedProvider,
    make_user: UserFactory,
) -> None:
    """The "existing word, wrong sense" case — the third resolution path."""
    owner = await make_user(phone="+989120009004")
    provider.senses["run"] = [
        MeaningSuggestion(
            native_meaning="دویدن",
            definition="to move using your legs, faster than walking",
            example="I run every morning.",
            context="Movement",
            part_of_speech="verb",
        )
    ]
    provider.enrichment = [
        MeaningSuggestion(
            native_meaning="اداره کردن",
            definition="to control or be in charge of a business",
            example="She runs a bakery.",
            context="Management",
            part_of_speech="verb",
        )
    ]

    job = await _plan(build_service, session_factory, template_root, owner.id)
    await _run(build_service, session_factory, job.id)

    assert [term for term, _ in provider.enrich_calls] == ["run"]

    async with session_factory() as session:
        item = (
            (
                await session.execute(
                    select(DeckBuildItemModel).where(DeckBuildItemModel.source_term == "run")
                )
            )
            .scalars()
            .one()
        )
        senses = (
            (
                await session.execute(
                    select(LexemeSenseModel)
                    .join(LexemeModel, LexemeModel.id == LexemeSenseModel.lexeme_id)
                    .where(LexemeModel.lemma == "run")
                    .order_by(LexemeSenseModel.position)
                )
            )
            .scalars()
            .all()
        )

    # The original sense is untouched and the new one appended after it: a deck
    # already pointing at position 0 must keep pointing at the same sense.
    assert [s.context for s in senses] == ["Movement", "Management"]
    assert item.selection == SenseSelection.EXPLICIT.value
    assert item.state == DeckBuildItemState.DONE.value
    assert item.enriched is True


async def test_enrichment_is_attempted_once_per_item_even_across_retries(
    build_service,
    session_factory: async_sessionmaker[AsyncSession],
    template_root: Path,
    provider: ScriptedProvider,
    make_user: UserFactory,
) -> None:
    """The guard against paying repeatedly for a sense the model cannot produce."""
    owner = await make_user(phone="+989120009005")
    provider.senses["run"] = [
        MeaningSuggestion(
            native_meaning="دویدن",
            definition="to move using your legs, faster than walking",
            example="I run every morning.",
            context="Movement",
            part_of_speech="verb",
        )
    ]
    provider.enrichment = []  # the word genuinely has no such sense

    job = await _plan(build_service, session_factory, template_root, owner.id)
    await _run(build_service, session_factory, job.id)
    await _run(build_service, session_factory, job.id)

    assert len(provider.enrich_calls) == 1

    async with session_factory() as session:
        item = (
            (
                await session.execute(
                    select(DeckBuildItemModel).where(DeckBuildItemModel.source_term == "run")
                )
            )
            .scalars()
            .one()
        )
    # A card was still written, from the sense we do have, and flagged.
    assert item.state == DeckBuildItemState.NEEDS_REVIEW.value
    assert item.word_id is not None


async def test_one_dead_word_does_not_stop_the_others(
    build_service,
    session_factory: async_sessionmaker[AsyncSession],
    template_root: Path,
    provider: ScriptedProvider,
    make_user: UserFactory,
) -> None:
    owner = await make_user(phone="+989120009006")
    provider.fail_for = {"tact"}

    job = await _plan(build_service, session_factory, template_root, owner.id)
    outcome = await _run(build_service, session_factory, job.id)

    assert outcome.done == 2
    assert outcome.retrying == 1

    async with session_factory() as session:
        item = (
            (
                await session.execute(
                    select(DeckBuildItemModel).where(DeckBuildItemModel.source_term == "tact")
                )
            )
            .scalars()
            .one()
        )
    assert item.state == DeckBuildItemState.PENDING.value
    assert item.attempts == 1
    assert item.next_attempt_at is not None, "a retry must wait rather than spin"
    assert "provider is down" in (item.last_error or "")


async def test_a_word_that_keeps_failing_is_left_failed_and_the_job_goes_partial(
    build_service,
    session_factory: async_sessionmaker[AsyncSession],
    template_root: Path,
    provider: ScriptedProvider,
    make_user: UserFactory,
) -> None:
    owner = await make_user(phone="+989120009007")
    provider.fail_for = {"tact"}
    job = await _plan(build_service, session_factory, template_root, owner.id)

    for _ in range(3):
        await _run(build_service, session_factory, job.id)
        await _elapse_backoff(session_factory)

    await _run(build_service, session_factory, job.id)

    async with session_factory() as session:
        repo = SqlAlchemyDeckBuildRepository(session)
        refreshed = await repo.get_job(job.id)
        counts = await repo.count_by_state(job.id)

    assert refreshed is not None
    assert refreshed.state is DeckBuildState.PARTIAL
    assert counts[DeckBuildItemState.FAILED] == 1
    # The other two resolved. Both land in `needs_review` rather than `done`
    # because nothing in this template distinguishes their senses, so the
    # first-sense fallback chose — which is exactly what a reviewer should see.
    resolved = counts.get(DeckBuildItemState.DONE, 0) + counts.get(
        DeckBuildItemState.NEEDS_REVIEW, 0
    )
    assert resolved == 2


async def test_a_crashed_workers_claim_is_reclaimed_after_the_timeout(
    build_service,
    session_factory: async_sessionmaker[AsyncSession],
    template_root: Path,
    make_user: UserFactory,
) -> None:
    """The resume story: a worker killed mid-item must not park the word forever."""
    owner = await make_user(phone="+989120009008")
    job = await _plan(build_service, session_factory, template_root, owner.id)

    # Simulate a worker that claimed a row and then died.
    async with session_factory() as session:
        await session.execute(
            update(DeckBuildItemModel)
            .where(DeckBuildItemModel.source_term == "tact")
            .values(
                state=DeckBuildItemState.RESOLVING.value,
                claimed_at=datetime.now(UTC) - timedelta(hours=2),
            )
        )
        await session.commit()

    outcome = await _run(build_service, session_factory, job.id)

    assert outcome.done == 3
    assert outcome.finished_state is DeckBuildState.COMPLETED


async def test_rerunning_a_finished_build_is_a_no_op(
    build_service,
    session_factory: async_sessionmaker[AsyncSession],
    template_root: Path,
    provider: ScriptedProvider,
    make_user: UserFactory,
) -> None:
    """``task_acks_late`` guarantees redelivery, so this is the ordinary case."""
    owner = await make_user(phone="+989120009009")
    job = await _plan(build_service, session_factory, template_root, owner.id)
    await _run(build_service, session_factory, job.id)
    calls_after_first = list(provider.calls)

    await _run(build_service, session_factory, job.id)

    assert provider.calls == calls_after_first
    async with session_factory() as session:
        words = (await session.execute(select(WordModel))).scalars().all()
    assert len(words) == 3, "a redelivered batch must not double the deck"


async def test_a_second_live_build_of_one_template_is_refused(
    build_service,
    session_factory: async_sessionmaker[AsyncSession],
    template_root: Path,
    make_user: UserFactory,
) -> None:
    """Two concurrent builds would race for the same items and double the deck."""
    from app.core.exceptions import ValidationError

    owner = await make_user(phone="+989120009010")
    await _plan(build_service, session_factory, template_root, owner.id)

    with pytest.raises(ValidationError, match="already planned"):
        await _plan(build_service, session_factory, template_root, owner.id)


async def test_resolving_in_small_batches_walks_the_deck_in_order(
    build_service,
    session_factory: async_sessionmaker[AsyncSession],
    template_root: Path,
    provider: ScriptedProvider,
    make_user: UserFactory,
) -> None:
    """Batching must not reorder the book — an Explore copy relies on card order."""
    owner = await make_user(phone="+989120009011")
    job = await _plan(build_service, session_factory, template_root, owner.id)

    first = await _run(build_service, session_factory, job.id, limit=1)
    assert first.has_more is True
    await _run(build_service, session_factory, job.id, limit=1)
    last = await _run(build_service, session_factory, job.id, limit=1)

    assert last.has_more is False
    assert provider.calls == ["run", "keen", "tact"]


async def _elapse_backoff(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Move retry deadlines into the past instead of sleeping through them."""
    async with session_factory() as session:
        await session.execute(
            update(DeckBuildItemModel).values(next_attempt_at=datetime.now(UTC) - timedelta(days=1))
        )
        await session.commit()


class StubDictionary:
    """A dictionary whose answers — and silences — the test controls."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.entries: dict[str, DictionaryEntry | None] = {}

    async def look_up(self, term: str) -> DictionaryEntry | None:
        self.calls.append(term)
        return self.entries.get(term)


async def test_a_built_card_carries_the_dictionarys_pronunciation(
    session_factory: async_sessionmaker[AsyncSession],
    template_root: Path,
    provider: ScriptedProvider,
    make_user: UserFactory,
) -> None:
    """IPA is carried from the dictionary, never asked of a model."""
    dictionary = StubDictionary()
    dictionary.entries = {
        "run": DictionaryEntry(term="run", phonetic="/rʌn/"),
        "keen": DictionaryEntry(term="keen", phonetic="/kiːn/"),
        # "tact" has an entry but no transcription — a real answer, not a miss.
        "tact": DictionaryEntry(term="tact", phonetic=""),
    }

    def make(session: AsyncSession) -> DeckBuildService:
        return _service_with(session, provider, dictionary)

    owner = await make_user(phone="+989120009020")
    job = await _plan(make, session_factory, template_root, owner.id)
    await _run(make, session_factory, job.id)

    async with session_factory() as session:
        words = {w.term: w for w in (await session.execute(select(WordModel))).scalars().all()}
        lexemes = {x.lemma: x for x in (await session.execute(select(LexemeModel))).scalars().all()}

    assert words["run"].phonetic == "/rʌn/"
    assert words["keen"].phonetic == "/kiːn/"
    # '' is a real answer — "this word has no IPA" — and must not read as unknown.
    assert words["tact"].phonetic == ""
    assert lexemes["run"].phonetic == "/rʌn/"


async def test_a_dictionary_miss_leaves_the_word_open_for_a_later_retry(
    session_factory: async_sessionmaker[AsyncSession],
    template_root: Path,
    provider: ScriptedProvider,
    make_user: UserFactory,
) -> None:
    """A miss and an outage are indistinguishable, so neither is recorded as ''.

    Writing '' here would let one bad afternoon permanently silence a set of
    cards — the nightly backfill would never look at them again.
    """
    dictionary = StubDictionary()  # every lookup returns None

    def make(session: AsyncSession) -> DeckBuildService:
        return _service_with(session, provider, dictionary)

    owner = await make_user(phone="+989120009021")
    job = await _plan(make, session_factory, template_root, owner.id)
    outcome = await _run(make, session_factory, job.id)

    assert outcome.done == 3, "a dictionary that answers nothing must not fail the build"
    async with session_factory() as session:
        lexemes = (await session.execute(select(LexemeModel))).scalars().all()
    assert all(x.phonetic is None for x in lexemes)


async def test_no_dictionary_configured_still_builds_the_deck(
    session_factory: async_sessionmaker[AsyncSession],
    template_root: Path,
    provider: ScriptedProvider,
    make_user: UserFactory,
) -> None:
    """Pronunciation is optional; a card without one is what hand-typing yields."""

    def make(session: AsyncSession) -> DeckBuildService:
        return _service_with(session, provider, None)

    owner = await make_user(phone="+989120009022")
    job = await _plan(make, session_factory, template_root, owner.id)
    outcome = await _run(make, session_factory, job.id)

    assert outcome.done == 3
    async with session_factory() as session:
        words = (await session.execute(select(WordModel))).scalars().all()
    assert all(w.phonetic is None for w in words)


def _service_with(
    session: AsyncSession,
    provider: ScriptedProvider,
    dictionary: StubDictionary | None,
) -> DeckBuildService:
    lexicon = LexiconService(SqlAlchemyLexiconRepository(session), content_version=CONTENT_VERSION)
    return DeckBuildService(
        SqlAlchemyDeckBuildRepository(session),
        SqlAlchemyDeckRepository(session),
        SqlAlchemyDeckMemberRepository(session),
        SqlAlchemyDeckUnitRepository(session),
        SqlAlchemyWordRepository(session),
        SqlAlchemyDeckDiscoveryRepository(session),
        lexicon,
        provider,
        content_version=CONTENT_VERSION,
        enricher=provider,
        dictionary=dictionary,  # type: ignore[arg-type]
    )


async def test_a_run_of_provider_failures_halts_the_build(
    build_service,
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    provider: ScriptedProvider,
    make_user: UserFactory,
) -> None:
    """One bad word must not stop the deck; a dead provider must.

    Found in production: a gateway started refusing every request, and the build
    marched on through the plan spending two wasted calls per word — the grounded
    attempt and its generative fallback — three times over.
    """
    base = tmp_path / "many"
    base.mkdir()
    (base / "deck.yaml").write_text(
        "slug: many\nname: Many\ncategory: general\n"
        "generation:\n  native_language: Persian\n"
        "structure:\n  expected_units: 1\n  expected_words: 20\n"
    )
    words = "\n".join(f"      - word{i}" for i in range(20))
    (base / "words.yaml").write_text(f"units:\n  - name: Unit 1\n    words:\n{words}\n")

    provider.fail_with = ExternalServiceError("The AI service is unavailable right now.")
    owner = await make_user(phone="+989120009030")

    template = load_template("many", root=tmp_path)
    async with session_factory() as session:
        job = await build_service(session).plan(template, owner_id=owner.id)
        await session.commit()

    outcome = await _run(build_service, session_factory, job.id, limit=20)

    assert outcome.finished_state is DeckBuildState.PARTIAL
    assert outcome.has_more is False
    # It stopped at the streak rather than walking all 20 words.
    assert outcome.processed <= PROVIDER_FAILURE_STREAK + 1
    assert len(provider.calls) <= PROVIDER_FAILURE_STREAK + 1

    async with session_factory() as session:
        refreshed = await SqlAlchemyDeckBuildRepository(session).get_job(job.id)
    assert refreshed is not None
    assert "consecutive provider failures" in (refreshed.last_error or "")


async def test_ordinary_word_failures_do_not_halt_the_build(
    build_service,
    session_factory: async_sessionmaker[AsyncSession],
    template_root: Path,
    provider: ScriptedProvider,
    make_user: UserFactory,
) -> None:
    """A word the model simply cannot describe is not an outage."""
    provider.fail_for = {"tact"}  # raises RuntimeError, not ExternalServiceError
    owner = await make_user(phone="+989120009031")
    job = await _plan(build_service, session_factory, template_root, owner.id)

    outcome = await _run(build_service, session_factory, job.id)

    assert outcome.done == 2, "the other words must still be built"
    assert outcome.finished_state is not DeckBuildState.PARTIAL


# ── presentation: the logo, and re-reading a template ────────


async def test_a_templates_icon_lands_on_the_deck_it_builds(
    build_service,
    session_factory: async_sessionmaker[AsyncSession],
    template_root: Path,
    make_user: UserFactory,
) -> None:
    """The icon slug reaches the deck row, where the client reads it.

    Without this the badge falls back to the deck's initial, which for a course
    is a letter that means nothing — "5" for 504.
    """
    owner = await make_user(phone="+989120009040")
    job = await _plan(build_service, session_factory, template_root, owner.id)

    async with session_factory() as session:
        deck = await session.get(DeckModel, job.deck_id)
        assert deck is not None
        assert deck.icon == "tiny"


async def test_a_deck_a_learner_built_has_no_icon(
    session_factory: async_sessionmaker[AsyncSession],
    make_user: UserFactory,
) -> None:
    """Empty, not a placeholder: an ordinary deck draws its initial as always."""
    owner = await make_user(phone="+989120009041")
    async with session_factory() as session:
        deck = DeckModel(user_id=owner.id, name="My words", hue=25)
        session.add(deck)
        await session.commit()
        assert deck.icon == ""


async def test_sync_metadata_rewords_a_published_deck_without_unpublishing_it(
    build_service,
    session_factory: async_sessionmaker[AsyncSession],
    template_root: Path,
    make_user: UserFactory,
) -> None:
    """Editing a template's copy in git must reach a deck built months ago.

    Templates are read once, at plan time, so without this the only way to fix
    a description would be to rebuild — re-buying every card in the deck. The
    property that makes it safe to run against a live deck is the second half:
    ``is_public`` is not among the things it touches.
    """
    owner = await make_user(phone="+989120009042")
    job = await _plan(build_service, session_factory, template_root, owner.id)
    await _run(build_service, session_factory, job.id)

    # The deck goes live, as an admin would publish it.
    async with session_factory() as session:
        await session.execute(
            update(DeckModel)
            .where(DeckModel.id == job.deck_id)
            .values(is_public=True, published_at=datetime.now(UTC))
        )
        await session.commit()

    # The word list is untouched; only the wording and the logo change.
    (template_root / "tiny" / "deck.yaml").write_text(
        DECK_YAML.replace("A short deck.", "Shorter still.").replace("icon: tiny", "icon: tinier")
    )
    template = load_template("tiny", root=template_root)

    async with session_factory() as session:
        result = await build_service(session).sync_metadata(template)
        await session.commit()

    assert result.deck_id == job.deck_id
    assert result.changed["description"] == ("A short deck.", "Shorter still.")
    assert result.changed["icon"] == ("tiny", "tinier")
    assert "name" not in result.changed, "unchanged fields are not reported as changes"

    async with session_factory() as session:
        deck = await session.get(DeckModel, job.deck_id)
        assert deck is not None
        assert deck.description == "Shorter still."
        assert deck.icon == "tinier"
        assert deck.is_public is True, "re-wording a deck must never unpublish it"
        words = (
            (await session.execute(select(WordModel).where(WordModel.deck_id == job.deck_id)))
            .scalars()
            .all()
        )
        assert len(words) == 3, "the cards are what a rebuild would cost — none may be touched"
        assert {w.term for w in words} == {"run", "keen", "tact"}


async def test_sync_metadata_refuses_a_template_that_was_never_built(
    build_service,
    session_factory: async_sessionmaker[AsyncSession],
    template_root: Path,
) -> None:
    """There is no deck to sync onto, and inventing one would be a surprise."""
    template = load_template("tiny", root=template_root)
    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            await build_service(session).sync_metadata(template)
