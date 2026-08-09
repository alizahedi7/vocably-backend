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
from app.application.services.deck_build_service import DeckBuildService
from app.application.services.lexicon_service import LexiconService
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

    async def look_up_meanings(self, term: str, learner: LearnerContext) -> LookupResult:
        self.calls.append(term)
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
