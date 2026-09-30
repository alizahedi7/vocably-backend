"""The reader's Redis tier: a cache that can only ever save time.

What must hold: a round trip returns the same answer; an unreadable payload is
a miss; "not a word" is never stored here; and the first failure — a refused
connection or a slow command — turns the tier off for the process, because a
cache that waits out a timeout on every tap is a slower app, not a cache.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.application.ports.ai_service import (
    AIService,
    GeneratedStory,
    LearnerContext,
    LookupResult,
    LookupStatus,
    MeaningSuggestion,
)
from app.application.ports.reader_ai import ContextualMeaning
from app.infrastructure.ai.reader_hot_cache import HotCachingAIService, ReaderHotCache

LEARNER = LearnerContext(native_language="Persian")
RESULT = LookupResult(
    term="bank",
    suggestions=[MeaningSuggestion("بانک", "a place for money", "e.g.", "Finance", "noun")],
    phonetic="/bæŋk/",
    provider="avalai",
    model="m",
)


class FakeRedis:
    def __init__(self, *, fail: Exception | None = None, delay: float = 0.0) -> None:
        self.store: dict[str, str] = {}
        self.calls = 0
        self._fail = fail
        self._delay = delay

    async def get(self, name: str) -> Any:
        return await self._op(lambda: self.store.get(name))

    async def set(self, name: str, value: str, *, ex: int = 0) -> Any:
        return await self._op(lambda: self.store.__setitem__(name, value))

    async def _op(self, action: Any) -> Any:
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._fail is not None:
            raise self._fail
        return action()


async def test_a_lookup_round_trips() -> None:
    cache = ReaderHotCache(FakeRedis())
    await cache.put_lookup("d", RESULT)
    assert await cache.get_lookup("d") == RESULT


async def test_not_a_word_is_never_stored() -> None:
    redis = FakeRedis()
    cache = ReaderHotCache(redis)
    await cache.put_lookup(
        "d", LookupResult(term="zzz", suggestions=[], status=LookupStatus.UNSUPPORTED)
    )
    assert redis.store == {}


async def test_an_unreadable_payload_is_a_miss() -> None:
    redis = FakeRedis()
    redis.store["reader:lookup:d"] = '{"term": "bank"}'
    assert await ReaderHotCache(redis).get_lookup("d") is None


async def test_a_meaning_is_memoised_per_word_sentence_and_language() -> None:
    cache = ReaderHotCache(FakeRedis())
    key = cache.meaning_key("Bank", "They sat on the Bank.", "Persian", 2)
    assert key == cache.meaning_key("bank", "they sat on the bank.", "persian", 2)
    assert key != cache.meaning_key("bank", "they sat on the bank.", "Persian", 3)
    assert key != cache.meaning_key("bank", "they sat on the bank.", "English", 2)
    assert await cache.get_meaning(key) is None
    meaning = ContextualMeaning("bank", "noun", "River", "the land by a river", "ساحل", "p", "m")
    await cache.put_meaning(key, meaning)
    assert await cache.get_meaning(key) == meaning


async def test_the_first_failure_turns_the_tier_off_for_the_process() -> None:
    redis = FakeRedis(fail=ConnectionError("refused"))
    cache = ReaderHotCache(redis)
    assert await cache.get_lookup("d") is None
    assert cache.is_disabled
    await cache.put_lookup("d", RESULT)
    assert await cache.get_meaning("k") is None
    assert redis.calls == 1  # never asked again


async def test_a_slow_command_counts_as_a_failure() -> None:
    redis = FakeRedis(delay=0.2)
    cache = ReaderHotCache(redis, op_timeout_seconds=0.01)
    assert await cache.get_lookup("d") is None
    assert cache.is_disabled


class Counting(AIService):
    def __init__(self) -> None:
        self.calls = 0

    async def look_up_meanings(self, term: str, learner: LearnerContext) -> LookupResult:
        self.calls += 1
        return RESULT

    async def generate_story(self, words: list[str], learner: LearnerContext) -> GeneratedStory:
        return GeneratedStory(text="", words_used=words)


async def test_the_decorator_serves_repeats_from_redis() -> None:
    inner = Counting()
    service = HotCachingAIService(inner, ReaderHotCache(FakeRedis()), prompt_version=2)
    assert await service.look_up_meanings("bank", LEARNER) == RESULT
    assert await service.look_up_meanings("Bank", LEARNER) == RESULT
    assert inner.calls == 1


async def test_the_decorator_still_answers_when_redis_is_down() -> None:
    inner = Counting()
    service = HotCachingAIService(
        inner, ReaderHotCache(FakeRedis(fail=ConnectionError("down"))), prompt_version=2
    )
    for _ in range(3):
        assert await service.look_up_meanings("bank", LEARNER) == RESULT
    assert inner.calls == 3
