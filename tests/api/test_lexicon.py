"""The lexicon layer, end to end through the real repository and database.

What only a database can prove: that a lookup writes durable rows, that the layer
answers when the cache cannot, and — the property the whole design rests on —
that a **prompt-version bump costs nothing** for a word already known. Without
this layer a prompt edit re-buys the entire corpus; the test at the bottom of
this file is the one that would catch that regressing.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_ai_provider
from app.application.ports.ai_service import (
    AIService,
    GeneratedStory,
    LearnerContext,
    LookupResult,
    LookupStatus,
    MeaningSuggestion,
)
from app.core.config import settings
from app.infrastructure.db.models.lexicon import (
    LexemeModel,
    LexemeSenseModel,
    LexemeSenseTranslationModel,
)
from app.main import app

from .conftest import UserFactory, bearer


class CountingProvider(AIService):
    """Stands in for the model so provider calls can be counted exactly."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.result: LookupResult | None = None

    async def look_up_meanings(self, term: str, learner: LearnerContext) -> LookupResult:
        self.calls.append(term)
        if self.result is not None:
            return self.result
        return LookupResult(
            term=term.strip().lower(),
            phonetic="/rʌn/",
            suggestions=[
                MeaningSuggestion(
                    native_meaning="دویدن",
                    definition="to move using your legs, faster than walking",
                    example="I run every morning.",
                    context="Movement",
                    part_of_speech="verb",
                ),
                MeaningSuggestion(
                    native_meaning="اداره کردن",
                    definition="to control or be in charge of a business",
                    example="She runs a bakery.",
                    context="Management",
                    part_of_speech="verb",
                ),
            ],
        )

    async def generate_story(self, words: list[str], learner: LearnerContext) -> GeneratedStory:
        return GeneratedStory(text="a story", words_used=words)


@pytest.fixture
def provider() -> CountingProvider:
    return CountingProvider()


@pytest.fixture
async def lexicon_client(
    client: AsyncClient,
    provider: CountingProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[AsyncClient, None]:
    # The lookup cache is off for most of this file: it sits directly above the
    # lexicon and would answer first, so leaving it on would make these
    # assertions measure the cache instead. One test below turns it back on
    # precisely to check how the two compose.
    monkeypatch.setattr(settings, "ai_cache_enabled", False)
    monkeypatch.setattr(settings, "lexicon_enabled", True)
    monkeypatch.setattr(settings, "lexicon_single_flight", False)
    app.dependency_overrides[get_ai_provider] = lambda: provider
    yield client
    app.dependency_overrides.pop(get_ai_provider, None)


async def _count(session_factory: async_sessionmaker[AsyncSession], model: object) -> int:
    async with session_factory() as session:
        return int(
            (await session.execute(select(func.count()).select_from(model))).scalar_one()  # type: ignore[arg-type]
        )


async def test_a_lookup_writes_every_sense_as_durable_shared_content(
    lexicon_client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    response = await lexicon_client.post(
        "/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"}
    )
    assert response.status_code == 200, response.text

    assert await _count(session_factory, LexemeModel) == 1
    # Both senses, not only the one a learner might pick: the next deck needs the
    # whole set to choose from.
    assert await _count(session_factory, LexemeSenseModel) == 2
    assert await _count(session_factory, LexemeSenseTranslationModel) == 2

    async with session_factory() as session:
        lexeme = (await session.execute(select(LexemeModel))).scalars().one()
    assert lexeme.lemma == "run"
    assert lexeme.phonetic == "/rʌn/"


async def test_a_repeat_lookup_is_served_from_the_lexicon_without_a_provider_call(
    lexicon_client: AsyncClient,
    auth_headers: dict[str, str],
    provider: CountingProvider,
) -> None:
    for _ in range(3):
        response = await lexicon_client.post(
            "/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"}
        )
        assert response.status_code == 200

    assert provider.calls == ["run"]
    assert len(response.json()["suggestions"]) == 2


async def test_the_lexicon_is_shared_across_users(
    lexicon_client: AsyncClient,
    auth_headers: dict[str, str],
    provider: CountingProvider,
    make_user: UserFactory,
) -> None:
    """Nothing here is keyed by a learner — one person's lookup pays for everyone's."""
    await lexicon_client.post("/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"})

    other = await make_user(phone="+989120000123")
    response = await lexicon_client.post(
        "/api/v1/ai/lookup", headers=bearer(other.id), json={"term": "run"}
    )

    assert response.status_code == 200
    assert provider.calls == ["run"]


async def test_a_prompt_version_bump_costs_nothing_for_a_word_already_known(
    lexicon_client: AsyncClient,
    auth_headers: dict[str, str],
    provider: CountingProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reason the lexicon sits *inside* the cache rather than outside it.

    A prompt bump retires every cached card by design. If the lexicon were the
    outer layer, that bump would re-buy the entire corpus. Here the cache misses,
    the lexicon answers, and the bill is zero.
    """
    monkeypatch.setattr(settings, "ai_cache_enabled", True)
    await lexicon_client.post("/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"})
    assert provider.calls == ["run"]

    # Retire the whole cache, exactly as editing a prompt does.
    import app.infrastructure.ai.factory as factory

    monkeypatch.setattr(factory, "PROMPT_VERSION", 9999)

    response = await lexicon_client.post(
        "/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"}
    )

    assert response.status_code == 200
    assert provider.calls == ["run"], "a prompt bump must not re-buy a known word"
    assert len(response.json()["suggestions"]) == 2


async def test_unintelligible_input_is_never_recorded_as_a_word(
    lexicon_client: AsyncClient,
    auth_headers: dict[str, str],
    provider: CountingProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The lexicon is a record of words. "asdfgh" is not one.

    Remembering that verdict is the lookup cache's job, with a TTL — here it
    would be a permanent row asserting a non-word exists.
    """
    provider.result = LookupResult(
        term="asdfgh",
        suggestions=[],
        status=LookupStatus.UNSUPPORTED,
        notice="That doesn't look like a word.",
    )
    response = await lexicon_client.post(
        "/api/v1/ai/lookup", headers=auth_headers, json={"term": "asdfgh"}
    )

    assert response.status_code == 200
    assert await _count(session_factory, LexemeModel) == 0


async def test_a_sense_that_fails_validation_never_becomes_shared_content(
    lexicon_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    provider: CountingProvider,
    make_user: UserFactory,
) -> None:
    """The learner is still served; the platform just does not adopt the answer."""
    # A Persian learner, so "eager" as a headline is the wrong-script failure —
    # well-formed, plausible in a log, and useless on a card.
    learner = await make_user(phone="+989120000456", native_language="Persian")
    auth_headers = bearer(learner.id)
    provider.result = LookupResult(
        term="keen",
        suggestions=[
            MeaningSuggestion(
                # A "translation" written in English — well-formed and useless.
                native_meaning="eager",
                definition="wanting to do something very much",
                example="She is keen to help.",
                context="Enthusiasm",
                part_of_speech="adjective",
            )
        ],
    )
    response = await lexicon_client.post(
        "/api/v1/ai/lookup", headers=auth_headers, json={"term": "keen"}
    )

    assert response.status_code == 200
    assert response.json()["suggestions"][0]["native_meaning"] == "eager"
    assert await _count(session_factory, LexemeSenseModel) == 0


async def test_a_typo_and_the_word_share_one_lexeme(
    lexicon_client: AsyncClient,
    auth_headers: dict[str, str],
    provider: CountingProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The lexeme is keyed by the *resolved* term, so a correction costs nothing extra."""
    provider.result = LookupResult(
        term="run",
        status=LookupStatus.CORRECTED,
        notice="Showing results for “run”.",
        suggestions=[
            MeaningSuggestion(
                native_meaning="دویدن",
                definition="to move using your legs, faster than walking",
                example="I run every morning.",
                context="Movement",
                part_of_speech="verb",
            )
        ],
    )
    await lexicon_client.post("/api/v1/ai/lookup", headers=auth_headers, json={"term": "rnu"})
    provider.result = None
    await lexicon_client.post("/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"})

    assert await _count(session_factory, LexemeModel) == 1
    assert provider.calls == ["rnu"]


async def test_disabling_the_lexicon_restores_a_provider_call_per_lookup(
    lexicon_client: AsyncClient,
    auth_headers: dict[str, str],
    provider: CountingProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "lexicon_enabled", False)

    for _ in range(2):
        await lexicon_client.post("/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"})

    assert provider.calls == ["run", "run"]
