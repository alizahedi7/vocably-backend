"""The AI lookup cache, end to end through the real repository and database.

The unit tests pin the decorator's logic against fakes; these pin the parts only
a database can prove — that the two tables are written as designed, that a
concurrent writer does not blow up the request, and that nothing here is scoped
to a user.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

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
from app.infrastructure.db.models.ai_lookup import AILookupAliasModel, AILookupEntryModel
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

    async def generate_story(self, words: list[str], learner: LearnerContext) -> GeneratedStory:
        return GeneratedStory(text="a story", words_used=words)


@pytest.fixture
def provider() -> CountingProvider:
    return CountingProvider()


@pytest.fixture
async def cached_client(
    client: AsyncClient,
    provider: CountingProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[AsyncClient, None]:
    # The lexicon is switched off for this whole file. It sits directly beneath
    # the cache and absorbs exactly the calls these tests exist to count, so
    # leaving it on would make every provider-call assertion here a measurement
    # of two layers at once. The lexicon's own behaviour is pinned in
    # test_lexicon.py, including how the two compose.
    monkeypatch.setattr(settings, "lexicon_enabled", False)
    app.dependency_overrides[get_ai_provider] = lambda: provider
    yield client
    app.dependency_overrides.pop(get_ai_provider, None)


async def _count(session_factory: async_sessionmaker[AsyncSession], model: Any) -> int:
    async with session_factory() as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_repeat_lookup_is_served_from_the_database_without_a_provider_call(
    cached_client: AsyncClient,
    auth_headers: dict[str, str],
    provider: CountingProvider,
) -> None:
    first = await cached_client.post(
        "/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"}
    )
    second = await cached_client.post(
        "/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"}
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert provider.calls == ["run"]
    # The cached card is byte-identical to the one the provider produced.
    assert second.json() == first.json()


async def test_the_cache_is_shared_across_users(
    cached_client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    provider: CountingProvider,
) -> None:
    """The whole point: one learner's lookup pays for everyone else's."""
    await cached_client.post("/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"})

    other = await make_user(phone="+989120000002")
    response = await cached_client.post(
        "/api/v1/ai/lookup", headers=bearer(other.id), json={"term": "run"}
    )

    assert response.status_code == 200
    assert provider.calls == ["run"]
    assert response.json()["suggestions"][0]["definition"].startswith("to move using your legs")


async def test_spelling_variants_share_one_paid_for_entry(
    cached_client: AsyncClient,
    auth_headers: dict[str, str],
    provider: CountingProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider.result = LookupResult(
        term="run",
        suggestions=[
            MeaningSuggestion(
                native_meaning="to run", definition="to move fast on foot", example="I run."
            )
        ],
        status=LookupStatus.CORRECTED,
        notice="Showing results for 'run'",
    )

    for term in ("runing", "runn"):
        response = await cached_client.post(
            "/api/v1/ai/lookup", headers=auth_headers, json={"term": term}
        )
        assert response.status_code == 200
        assert response.json()["notice"] == "Showing results for 'run'"

    # Two distinct typos, but only one copy of the senses was stored.
    assert await _count(session_factory, AILookupAliasModel) == 2
    assert await _count(session_factory, AILookupEntryModel) == 1


async def test_a_cached_entry_carries_no_user_scope(
    cached_client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The cache is a shared dictionary, not user data."""
    await cached_client.post("/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"})

    async with session_factory() as session:
        entry = (await session.execute(select(AILookupEntryModel))).scalars().one()

    assert not hasattr(entry, "user_id")
    assert entry.term == "run"
    assert entry.provider == "stub"


async def test_unsupported_input_is_cached_with_an_expiry(
    cached_client: AsyncClient,
    auth_headers: dict[str, str],
    provider: CountingProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider.result = LookupResult(
        term="qwrtyp",
        suggestions=[],
        status=LookupStatus.UNSUPPORTED,
        notice="That doesn't look like a word we can define.",
    )

    for _ in range(3):
        response = await cached_client.post(
            "/api/v1/ai/lookup", headers=auth_headers, json={"term": "qwrtyp"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "unsupported"
        assert response.json()["suggestions"] == []

    # Retry-spam costs one provider call, and buys no permanent row.
    assert provider.calls == ["qwrtyp"]
    assert await _count(session_factory, AILookupEntryModel) == 0
    async with session_factory() as session:
        alias = (await session.execute(select(AILookupAliasModel))).scalars().one()
    assert alias.entry_id is None
    assert alias.expires_at is not None


async def test_long_input_caches_the_resolved_term_but_not_the_sentence(
    cached_client: AsyncClient,
    auth_headers: dict[str, str],
    provider: CountingProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A sentence is never typed twice, but the word inside it is."""
    sentence = "I was absolutely running as fast as I possibly could"
    provider.result = LookupResult(
        term="run",
        suggestions=[
            MeaningSuggestion(
                native_meaning="to run", definition="to move fast on foot", example="I run."
            )
        ],
        status=LookupStatus.EXTRACTED,
        notice="Showing results for 'run'",
    )

    await cached_client.post("/api/v1/ai/lookup", headers=auth_headers, json={"term": sentence})

    assert await _count(session_factory, AILookupAliasModel) == 0
    assert await _count(session_factory, AILookupEntryModel) == 1

    # The next learner who looks up the word itself inherits that entry.
    provider.result = None
    response = await cached_client.post(
        "/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"}
    )
    assert response.status_code == 200
    assert await _count(session_factory, AILookupEntryModel) == 1


async def test_a_writer_that_loses_the_race_adopts_the_winning_row(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two learners looking up a brand-new word at once is normal, not an error.

    The losing writer's ``INSERT`` violates the unique constraint *after* its
    existence check passed. Forced deterministically here by blinding that check,
    because a real race cannot be scheduled reliably — and because the failure it
    guards against (a 500 on a lookup that already succeeded) is silent otherwise.
    """
    from app.application.ports.lookup_cache import build_lookup_cache_key
    from app.infrastructure.db.repositories import lookup_cache_repository
    from app.infrastructure.db.repositories.lookup_cache_repository import (
        SqlAlchemyLookupCacheRepository,
    )

    key = build_lookup_cache_key("borrow", LearnerContext(), prompt_version=1)
    result = LookupResult(
        term="borrow",
        suggestions=[
            MeaningSuggestion(
                native_meaning="to borrow", definition="to take on loan", example="I borrow."
            )
        ],
    )

    async with session_factory() as session:
        repo = SqlAlchemyLookupCacheRepository(session)
        await repo.put(key, result)
        await session.commit()

    async def _always_missing(self: object, digest: str) -> None:
        return None

    monkeypatch.setattr(
        lookup_cache_repository.SqlAlchemyLookupCacheRepository, "_entry_id", _always_missing
    )
    monkeypatch.setattr(
        lookup_cache_repository.SqlAlchemyLookupCacheRepository,
        "_alias_exists",
        lambda self, digest: _false(),
    )

    async with session_factory() as session:
        repo = SqlAlchemyLookupCacheRepository(session)
        # Must not raise: the row is already there, which is success, not failure.
        await repo.put(key, result)
        await session.commit()

    assert await _count(session_factory, AILookupEntryModel) == 1
    assert await _count(session_factory, AILookupAliasModel) == 1


async def _false() -> bool:
    return False


async def test_a_cached_row_this_deploy_cannot_read_is_treated_as_a_miss(
    cached_client: AsyncClient,
    auth_headers: dict[str, str],
    provider: CountingProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Stored shape is no more trustworthy than a provider's — half a card is worse than none."""
    await cached_client.post("/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"})
    assert provider.calls == ["run"]

    async with session_factory() as session:
        entry = (await session.execute(select(AILookupEntryModel))).scalars().one()
        entry.payload = {"v": 999, "senses": [{"meaning": "from the future"}]}
        await session.commit()

    response = await cached_client.post(
        "/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"}
    )

    assert response.status_code == 200
    assert provider.calls == ["run", "run"]
    assert response.json()["suggestions"][0]["definition"].startswith("to move using your legs")


async def test_repeat_hits_increment_the_entrys_hit_count(
    cached_client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    for _ in range(3):
        response = await cached_client.post(
            "/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"}
        )
        assert response.status_code == 200

    async with session_factory() as session:
        entry = (await session.execute(select(AILookupEntryModel))).scalars().one()

    # The write on miss doesn't count as a hit; the two repeats do.
    assert entry.hit_count == 2
    assert entry.last_accessed_at is not None


async def test_a_cache_miss_never_touches_hit_count(
    cached_client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await cached_client.post("/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"})

    async with session_factory() as session:
        entry = (await session.execute(select(AILookupEntryModel))).scalars().one()

    assert entry.hit_count == 0
    assert entry.last_accessed_at is None


async def test_disabling_the_cache_restores_a_provider_call_per_lookup(
    cached_client: AsyncClient,
    auth_headers: dict[str, str],
    provider: CountingProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ai_cache_enabled", False)

    for _ in range(2):
        await cached_client.post("/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"})

    assert provider.calls == ["run", "run"]
