"""The cache decorator's contract with the provider behind it."""

from __future__ import annotations

import pytest

from app.application.ports.ai_service import (
    AIService,
    GeneratedStory,
    LearnerContext,
    LookupResult,
    LookupStatus,
    MeaningSuggestion,
)
from app.application.ports.lookup_cache import LookupCacheKey, LookupCacheRepository
from app.infrastructure.ai.caching_ai_service import CachingAIService

_SENSE = MeaningSuggestion(
    native_meaning="to run",
    definition="to move fast on foot",
    example="I run.",
    context="Movement",
    part_of_speech="verb",
)


class RecordingProvider(AIService):
    def __init__(self, result: LookupResult | None = None) -> None:
        self.calls = 0
        self.story_calls = 0
        self._result = result or LookupResult(term="run", suggestions=[_SENSE])

    async def look_up_meanings(self, term: str, learner: LearnerContext) -> LookupResult:
        self.calls += 1
        return self._result

    async def generate_story(self, words: list[str], learner: LearnerContext) -> GeneratedStory:
        self.story_calls += 1
        return GeneratedStory(text="a story", words_used=words)


class InMemoryCache(LookupCacheRepository):
    def __init__(self) -> None:
        self.rows: dict[str, LookupResult] = {}
        self.ttls: list[int | None] = []

    async def get(self, key: LookupCacheKey) -> LookupResult | None:
        return self.rows.get(key.digest()) if key.is_aliasable else None

    async def put(
        self,
        key: LookupCacheKey,
        result: LookupResult,
        *,
        alias_ttl_seconds: int | None = None,
        provider: str = "",
        model: str = "",
    ) -> None:
        self.ttls.append(alias_ttl_seconds)
        if key.is_aliasable:
            self.rows[key.digest()] = result


class BrokenCache(LookupCacheRepository):
    """Every operation fails — the cache must never be load-bearing."""

    async def get(self, key: LookupCacheKey) -> LookupResult | None:
        raise RuntimeError("database is on fire")

    async def put(self, key: LookupCacheKey, result: LookupResult, **kwargs: object) -> None:
        raise RuntimeError("database is on fire")


def _service(provider: AIService, cache: LookupCacheRepository) -> CachingAIService:
    return CachingAIService(provider, cache, unsupported_ttl_seconds=604800)


async def test_second_lookup_of_the_same_term_never_reaches_the_provider() -> None:
    provider = RecordingProvider()
    service = _service(provider, InMemoryCache())

    first = await service.look_up_meanings("run", LearnerContext())
    second = await service.look_up_meanings("run", LearnerContext())

    assert provider.calls == 1
    assert first == second


async def test_every_sense_is_cached_not_just_a_chosen_one() -> None:
    """The next learner needs the whole deck to choose from."""
    senses = [
        _SENSE,
        MeaningSuggestion(
            native_meaning="a jog",
            definition="a period of running",
            example="a run",
            context="Sport",
            part_of_speech="noun",
        ),
    ]
    provider = RecordingProvider(LookupResult(term="run", suggestions=senses))
    service = _service(provider, InMemoryCache())

    await service.look_up_meanings("run", LearnerContext())
    cached = await service.look_up_meanings("run", LearnerContext())

    assert cached.suggestions == senses


async def test_typo_and_clean_spelling_are_separate_hits_with_their_own_notice() -> None:
    corrected = LookupResult(
        term="run",
        suggestions=[_SENSE],
        status=LookupStatus.CORRECTED,
        notice="Showing results for 'run'",
    )
    provider = RecordingProvider(corrected)
    service = _service(provider, InMemoryCache())

    await service.look_up_meanings("runing", LearnerContext())
    hit = await service.look_up_meanings("runing", LearnerContext())

    assert provider.calls == 1
    assert hit.status is LookupStatus.CORRECTED
    assert hit.notice == "Showing results for 'run'"
    assert hit.term == "run"


async def test_different_native_language_does_not_share_a_cached_card() -> None:
    provider = RecordingProvider()
    service = _service(provider, InMemoryCache())

    await service.look_up_meanings("run", LearnerContext(native_language="English"))
    await service.look_up_meanings("run", LearnerContext(native_language="Persian"))

    assert provider.calls == 2


async def test_only_unsupported_results_are_given_a_ttl() -> None:
    cache = InMemoryCache()
    unsupported = LookupResult(term="asdfgh", suggestions=[], status=LookupStatus.UNSUPPORTED)

    await _service(RecordingProvider(), cache).look_up_meanings("run", LearnerContext())
    await _service(RecordingProvider(unsupported), cache).look_up_meanings(
        "asdfgh", LearnerContext()
    )

    assert cache.ttls == [None, 604800]


async def test_a_broken_cache_never_breaks_a_lookup() -> None:
    provider = RecordingProvider()
    service = _service(provider, BrokenCache())

    result = await service.look_up_meanings("run", LearnerContext())

    assert result.term == "run"
    assert provider.calls == 1


async def test_stories_are_never_cached() -> None:
    provider = RecordingProvider()
    service = _service(provider, InMemoryCache())

    await service.generate_story(["run"], LearnerContext())
    await service.generate_story(["run"], LearnerContext())

    assert provider.story_calls == 2


@pytest.mark.parametrize("term", ["run", "RUN", "  run  ", "“Run”"])
async def test_spelling_noise_hits_the_same_cached_card(term: str) -> None:
    provider = RecordingProvider()
    service = _service(provider, InMemoryCache())

    await service.look_up_meanings("run", LearnerContext())
    await service.look_up_meanings(term, LearnerContext())

    assert provider.calls == 1
