"""No pooled connection is held while a gateway is thinking.

A request's session checks its connection out at the first query and used to
keep it, idle in transaction, through the whole provider call — so a burst of
cold lookups emptied the pool and stalled every other route. Postgres only:
SQLite's StaticPool has one connection and no checkout count to read.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.deps import get_ai_provider
from app.application.ports.ai_service import (
    AIService,
    GeneratedStory,
    LearnerContext,
    LookupResult,
    MeaningSuggestion,
)
from app.main import app


class PoolWatchingProvider(AIService):
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self.checked_out: list[int] = []

    async def look_up_meanings(self, term: str, learner: LearnerContext) -> LookupResult:
        self.checked_out.append(self._engine.pool.checkedout())  # type: ignore[attr-defined]
        return LookupResult(
            term=term,
            suggestions=[MeaningSuggestion("معنی", "a sense", "An example.", "General", "noun")],
        )

    async def generate_story(self, words: list[str], learner: LearnerContext) -> GeneratedStory:
        return GeneratedStory(text="", words_used=words)


@pytest.fixture
async def provider(engine: AsyncEngine) -> AsyncGenerator[PoolWatchingProvider, None]:
    if not hasattr(engine.pool, "checkedout") or engine.dialect.name != "postgresql":
        pytest.skip("needs a real connection pool (TEST_DATABASE_URL)")
    watching = PoolWatchingProvider(engine)
    app.dependency_overrides[get_ai_provider] = lambda: watching
    yield watching
    app.dependency_overrides.pop(get_ai_provider, None)


async def test_a_cold_lookup_holds_no_connection_during_the_provider_call(
    client: AsyncClient, auth_headers: dict[str, str], provider: PoolWatchingProvider
) -> None:
    response = await client.post("/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"})
    assert response.status_code == 200, response.text
    # The user and the cache were read first; the answer was still written after.
    assert provider.checked_out == [0]
    again = await client.post("/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"})
    assert again.json()["suggestions"], "the cache write after the call must still land"
    assert len(provider.checked_out) == 1
