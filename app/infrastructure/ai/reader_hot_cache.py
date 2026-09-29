"""Redis in front of the reader's two repeated questions. Never load-bearing.

The design's "Level 1". It sits *outside* ``CachingAIService`` for the reader
only, keyed by the very same ``LookupCacheKey`` digest, so a hit here and a hit
in ``ai_lookup_entries`` are the same answer — this layer only skips the
Postgres round trip for the words a whole class is tapping in the same chapter
this week.

It relies on a TTL rather than invalidation because the lexicon is append-only:
what goes stale is at worst a sense enriched an hour ago, never a wrong one.

It also holds the disambiguation memo. A sentence in a public-domain book is
the same sentence for every learner, so "which sense of *bound* does this line
use" is bought once per (sense deck, sentence) and shared.

**One strike and it is off**, exactly like ``SingleFlight``. Every command is
bounded by a short timeout, and the first failure disables Redis here for the
rest of the process. A best-effort cache that waits out a connect timeout on
every tap is a slower app, not a cache, and a command cancelled mid-flight can
leave a pooled connection that blocks the next caller. Giving up costs one
indexed Postgres read per tap — money and time, never correctness.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable
from dataclasses import asdict
from typing import Any, Protocol

from app.application.ports.ai_service import (
    AIService,
    GeneratedStory,
    LearnerContext,
    LookupResult,
    LookupStatus,
    MeaningSuggestion,
)
from app.application.ports.lookup_cache import build_lookup_cache_key
from app.core.logging import get_logger

logger = get_logger("vocably.reader.hotcache")

#: Hard ceiling on one Redis round trip, for the reason ``SingleFlight`` gives.
OP_TIMEOUT_SECONDS = 1.0


class AsyncRedisLike(Protocol):
    async def get(self, name: str) -> Any: ...
    async def set(self, name: str, value: str, *, ex: int = ...) -> Any: ...


class ReaderHotCache:
    def __init__(
        self,
        redis: AsyncRedisLike,
        *,
        lookup_ttl_seconds: int = 6 * 3600,
        disambiguation_ttl_seconds: int = 30 * 24 * 3600,
        op_timeout_seconds: float = OP_TIMEOUT_SECONDS,
    ) -> None:
        self._redis = redis
        self._lookup_ttl = lookup_ttl_seconds
        self._disambiguation_ttl = disambiguation_ttl_seconds
        self._op_timeout = op_timeout_seconds
        #: Flipped permanently by the first failure. See :meth:`_give_up`.
        self._disabled = False

    @property
    def is_disabled(self) -> bool:
        return self._disabled

    # ── Lookups ───────────────────────────────────────────────

    async def get_lookup(self, digest: str) -> LookupResult | None:
        raw = await self._call(self._redis.get(f"reader:lookup:{digest}"))
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return LookupResult(
                term=str(data["term"]),
                suggestions=[MeaningSuggestion(**s) for s in data["suggestions"]],
                status=LookupStatus(data.get("status", "ok")),
                notice=data.get("notice"),
                phonetic=str(data.get("phonetic", "")),
                provider=str(data.get("provider", "")),
                model=str(data.get("model", "")),
            )
        except (ValueError, KeyError, TypeError):
            # A payload this deploy cannot read is a miss, as in the DB cache.
            return None

    async def put_lookup(self, digest: str, result: LookupResult) -> None:
        if result.status is LookupStatus.UNSUPPORTED or not result.suggestions:
            # Not worth a key: the DB cache remembers "not a word" with its own TTL.
            return
        payload = {
            "term": result.term,
            "suggestions": [asdict(s) for s in result.suggestions],
            "status": result.status.value,
            "notice": result.notice,
            "phonetic": result.phonetic,
            "provider": result.provider,
            "model": result.model,
        }
        await self._call(
            self._redis.set(f"reader:lookup:{digest}", json.dumps(payload), ex=self._lookup_ttl)
        )

    # ── Disambiguations ───────────────────────────────────────

    def disambiguation_key(self, lookup_id: str, sentence: str, prompt_version: int) -> str:
        sentence_digest = hashlib.sha256(sentence.casefold().encode()).hexdigest()
        return f"reader:sense:{prompt_version}:{lookup_id}:{sentence_digest}"

    async def get_disambiguation(self, key: str) -> int | None:
        raw = await self._call(self._redis.get(key))
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    async def put_disambiguation(self, key: str, index: int) -> None:
        await self._call(self._redis.set(key, str(index), ex=self._disambiguation_ttl))

    # ── Plumbing ──────────────────────────────────────────────

    async def _call(self, command: Awaitable[Any]) -> Any:
        if self._disabled:
            # The coroutine was built by the caller; close it unawaited cleanly.
            close = getattr(command, "close", None)
            if close is not None:
                close()
            return None
        try:
            return await asyncio.wait_for(command, timeout=self._op_timeout)
        except (Exception, TimeoutError) as exc:  # noqa: BLE001 — never fatal
            self._give_up(exc)
            return None

    def _give_up(self, exc: BaseException) -> None:
        self._disabled = True
        logger.warning(
            "reader hot cache disabled for this process after %s; taps are served from Postgres",
            type(exc).__name__,
        )

    async def aclose(self) -> None:
        closer = getattr(self._redis, "aclose", None)
        if closer is None:
            return
        try:
            await closer()
        except Exception as exc:  # noqa: BLE001 — teardown must not fail a task
            logger.info("hot cache close failed: %s", type(exc).__name__)


class HotCachingAIService(AIService):
    """The decorator that puts :class:`ReaderHotCache` in front of the chain.

    Composed **outside** ``lookup_chain()``, and only for the reader::

        HotCachingAIService                     # Redis, reader only
          └─ CachingAIService                   # ai_lookup_entries
              └─ LexiconAIService               # lexemes
                  └─ GroundedAIService → Failover → provider

    Stories pass straight through, as in ``CachingAIService``.
    """

    def __init__(self, inner: AIService, cache: ReaderHotCache, *, prompt_version: int) -> None:
        self._inner = inner
        self._cache = cache
        self._prompt_version = prompt_version

    async def look_up_meanings(self, term: str, learner: LearnerContext) -> LookupResult:
        digest = build_lookup_cache_key(term, learner, self._prompt_version).digest()
        if hit := await self._cache.get_lookup(digest):
            return hit
        result = await self._inner.look_up_meanings(term, learner)
        await self._cache.put_lookup(digest, result)
        return result

    async def generate_story(self, words: list[str], learner: LearnerContext) -> GeneratedStory:
        return await self._inner.generate_story(words, learner)
