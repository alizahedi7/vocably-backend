"""Builds the AI adapters and the versions that identify them, once per process.

Lives here rather than in ``app.api.deps`` for the same reason
``app.infrastructure.dictionary.factory`` does: there are two entry points into
this codebase now, and the deck-build worker has no FastAPI in it at all. Both
callers must get the *same* chain — a builder with a private path to the provider
would quietly stop reusing the cache and the lexicon, which is the whole point of
the pipeline.

Memoized where a connection pool is involved, so an HTTP client is not rebuilt
per request or per batch.
"""

from __future__ import annotations

from functools import lru_cache
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.ai_service import AIService
from app.application.services.ai_studio_service import MAX_LOOKUP_SUGGESTIONS
from app.application.services.lexicon_service import LexiconService
from app.core.config import settings
from app.infrastructure.ai.caching_ai_service import CachingAIService
from app.infrastructure.ai.grounded_ai_service import GroundedAIService, SenseTranslator
from app.infrastructure.ai.lexicon_ai_service import LexiconAIService
from app.infrastructure.ai.prompts import PROMPT_VERSION
from app.infrastructure.ai.single_flight import SingleFlight
from app.infrastructure.ai.stub_ai_service import StubAIService
from app.infrastructure.ai.translate_prompts import TRANSLATE_PROMPT_VERSION
from app.infrastructure.db.repositories.lookup_cache_repository import (
    SqlAlchemyLookupCacheRepository,
)
from app.infrastructure.dictionary.factory import dictionary_service


def raw_ai_provider() -> AIService:
    """The provider adapter itself, before grounding, lexicon or caching.

    Also the object used as the enricher and the translator: both are structural
    protocols, and reusing one adapter keeps one HTTP client and one set of
    response guardrails across every path.
    """
    if settings.ai_provider == "anthropic":
        if not settings.anthropic_api_key:
            raise RuntimeError("AI_PROVIDER=anthropic requires ANTHROPIC_API_KEY.")
        return _anthropic_ai_service()
    if settings.ai_provider == "avalai":
        if not settings.avalai_api_key or not settings.avalai_model:
            raise RuntimeError("AI_PROVIDER=avalai requires AVALAI_API_KEY and AVALAI_MODEL.")
        return _avalai_ai_service()
    return StubAIService()


def grounded_ai_provider() -> AIService:
    """The lookup pipeline below the lexicon: provider, grounded when enabled."""
    provider = raw_ai_provider()
    if not settings.dictionary_enabled:
        return provider
    return GroundedAIService(
        provider,
        dictionary_service(),
        translator=cast("SenseTranslator", provider),
        max_cards=MAX_LOOKUP_SUGGESTIONS,
        rewrite_definitions=settings.dictionary_rewrite_definitions,
    )


def lookup_chain(
    session: AsyncSession,
    lexicon: LexiconService,
    *,
    provider: AIService | None = None,
) -> AIService:
    """The whole lookup pipeline, assembled outermost-first.

    ``cache`` → ``lexicon`` → ``grounded`` → ``provider``, and the order is the
    design: the cache answers repeats for one indexed read, and the lexicon
    answers what the cache cannot — which, the day ``PROMPT_VERSION`` is bumped,
    is every request. Put the lexicon *outside* the cache instead and a prompt
    edit re-buys the entire corpus.

    Both the API and the deck-build worker call this. A builder assembling its
    own chain would quietly stop reusing what learners have already paid for,
    and the reuse ratio reported on every job would become a lie.

    ``provider`` is the base of the chain and defaults to the configured one.
    It is a parameter rather than a lookup so that FastAPI's ``get_ai_provider``
    dependency — and every test that overrides it to count calls — still decides
    what sits at the bottom.
    """
    service = provider if provider is not None else grounded_ai_provider()
    if settings.lexicon_enabled:
        service = LexiconAIService(service, lexicon, single_flight=single_flight())
    if not settings.ai_cache_enabled:
        return service
    return CachingAIService(
        service,
        SqlAlchemyLookupCacheRepository(session),
        unsupported_ttl_seconds=settings.ai_cache_unsupported_ttl_seconds,
        provider=settings.ai_provider,
        model=configured_model(),
        prompt_version=effective_prompt_version(),
    )


def effective_prompt_version() -> int:
    """The content version for the whole lookup pipeline, not one prompt.

    Grounded and generated cards read differently, so they must never share a
    cache key — otherwise flipping ``DICTIONARY_ENABLED`` would serve a mix of
    the two and make a rollback invisible. Encoding both versions in one integer
    keeps ``LookupCacheKey`` unchanged and lets either prompt be bumped
    independently.

    The same integer is stamped on every lexicon sense as its ``content_version``,
    which is what makes "written by an older pipeline" a reportable fact rather
    than a guess. Unlike the cache, a bump does not retire anything here.
    """
    if not settings.dictionary_enabled:
        return PROMPT_VERSION
    # The two grounded modes write visibly different card fronts — one shows the
    # dictionary's wording, the other a rewrite — so they get distinct keys too.
    mode = 2 if settings.dictionary_rewrite_definitions else 1
    return (PROMPT_VERSION * 1000 + TRANSLATE_PROMPT_VERSION) * 10 + mode


def configured_model() -> str:
    """The model name recorded on cached entries and senses, for provenance only."""
    if settings.ai_provider == "anthropic":
        return settings.anthropic_model
    if settings.ai_provider == "avalai":
        return settings.avalai_model
    return ""


@lru_cache
def single_flight() -> SingleFlight | None:
    """Process-wide, so one Redis connection pool serves every caller.

    ``None`` when disabled, which every caller reads as "generate" — the same
    behaviour an unreachable Redis produces, because this is a cost optimisation
    and never a correctness mechanism.
    """
    if not settings.lexicon_single_flight:
        return None
    from redis.asyncio import Redis

    return SingleFlight(
        Redis.from_url(
            settings.lexicon_redis_url,
            decode_responses=True,
            # Fail fast and do not retry. This lock only saves money; a client
            # that retries a refused connection with backoff would spend more
            # time than the provider call it is trying to avoid.
            socket_connect_timeout=1,
            socket_timeout=1,
            retry_on_error=[],
        )
    )


@lru_cache
def _anthropic_ai_service() -> AIService:
    # Cached so one HTTP client (and its connection pool) is shared across
    # requests instead of being rebuilt per lookup.
    from app.infrastructure.ai.anthropic_ai_service import AnthropicAIService

    return AnthropicAIService(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        base_url=settings.anthropic_base_url,
        timeout_seconds=settings.anthropic_timeout_seconds,
        max_tokens=settings.anthropic_max_tokens,
        extra_headers=settings.anthropic_header_map,
    )


@lru_cache
def _avalai_ai_service() -> AIService:
    from app.infrastructure.ai.avalai_ai_service import AvalAIService

    return AvalAIService(
        api_key=settings.avalai_api_key,
        model=settings.avalai_model,
        base_url=settings.avalai_base_url,
        timeout_seconds=settings.avalai_timeout_seconds,
        max_tokens=settings.avalai_max_tokens,
    )
