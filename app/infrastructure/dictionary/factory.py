"""Builds the dictionary adapter and its Redis cache, once per process.

Lives here rather than in ``app.api.deps`` because it has two callers now: the
request path, and the ``vocably.ai.backfill_phonetics`` worker, which has no
FastAPI in it at all.

Memoized for the same reason the provider adapters are — both the HTTP
connection pool and the Redis pool should outlive a single request. Redis is
created lazily and never awaited here, so an unreachable Redis costs nothing at
startup: every cache call is best-effort and a dead one degrades to calling the
dictionary directly.
"""

from __future__ import annotations

from functools import lru_cache

from app.application.ports.dictionary_service import DictionaryService
from app.core.config import settings


@lru_cache
def dictionary_service() -> DictionaryService:
    import httpx
    from redis.asyncio import Redis

    from app.infrastructure.dictionary.free_dictionary_service import (
        DictionaryCache,
        FreeDictionaryService,
    )

    return FreeDictionaryService(
        httpx.AsyncClient(),
        DictionaryCache(
            Redis.from_url(settings.dictionary_redis_url, decode_responses=True),
            hit_ttl_seconds=settings.dictionary_cache_ttl_seconds,
            miss_ttl_seconds=settings.dictionary_cache_miss_ttl_seconds,
        ),
        timeout_seconds=settings.dictionary_timeout_seconds,
    )
