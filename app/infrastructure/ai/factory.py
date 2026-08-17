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
from app.infrastructure.ai.failover_ai_service import FailoverAIService
from app.infrastructure.ai.grounded_ai_service import GroundedAIService, SenseTranslator
from app.infrastructure.ai.lexicon_ai_service import LexiconAIService
from app.infrastructure.ai.prompts import PROMPT_VERSION
from app.infrastructure.ai.providers import PROVIDERS
from app.infrastructure.ai.single_flight import SingleFlight
from app.infrastructure.ai.stub_ai_service import StubAIService
from app.infrastructure.ai.translate_prompts import TRANSLATE_PROMPT_VERSION
from app.infrastructure.db.repositories.lookup_cache_repository import (
    SqlAlchemyLookupCacheRepository,
)
from app.infrastructure.dictionary.factory import dictionary_service


def raw_ai_provider() -> AIService:
    """The gateway (or failover fleet) itself, before grounding, lexicon or caching.

    Also the object used as the enricher and the translator: both are structural
    protocols, and reusing one adapter keeps one HTTP client and one set of
    response guardrails across every path. ``FailoverAIService`` implements those
    protocols too, which is what makes enrichment and grounded translation fail
    over exactly as a plain lookup does.

    **Failover belongs here, at the bottom.** Wrapped at this level, the cache,
    the lexicon and the grounding layer above it never learn that more than one
    gateway exists — so one cached entry and one lexeme still serve every
    learner, whichever gateway happened to write them. Wrapping *outside* the
    cache would route a fallback around it and re-buy the corpus at the moment
    the app can least afford it.
    """
    if settings.ai_provider == "anthropic":
        if not settings.anthropic_api_key:
            raise RuntimeError("AI_PROVIDER=anthropic requires ANTHROPIC_API_KEY.")
        return _anthropic_ai_service()

    chain = settings.provider_chain
    if not chain:
        return StubAIService()
    if len(chain) == 1:
        # No wrapper when there is nothing to fall over to: one gateway behind a
        # failover loop is the same gateway plus a layer of logs.
        return provider_for(chain[0])
    return FailoverAIService(
        [provider_for(name) for name in chain],
        breaker_threshold=settings.ai_breaker_threshold,
        breaker_cooldown_seconds=settings.ai_breaker_cooldown_seconds,
        deadline_seconds=settings.ai_failover_deadline_seconds,
    )


def build_ai_provider() -> AIService:
    """The gateway the **deck-build pipeline** uses, before grounding or caching.

    A published course deck is written once and read by everyone who saves it, so
    it is worth a slower, better model than an interactive lookup is —
    ``tabitoken`` and ``agentrouter`` carry frontier Claude models and nothing
    cheap. ``AI_BUILD_PROVIDER`` selects that chain; unset, a build uses the
    request path's, which is what a laptop and the test suite want.

    Note what this does **not** change: a build still goes through
    ``lookup_chain``, so the cache and the lexicon are the same ones learners
    fill. Only the gateway at the bottom differs. A builder with a private path
    past those would stop reusing what learners have already paid for, and the
    reuse ratio reported on every job would become a lie.

    The corollary is worth knowing before configuring this: a word already in the
    cache or the lexicon is served from there, so the build model writes only the
    words nobody has looked up yet. That is the intended trade — the alternative
    re-buys the corpus per deck — but it does mean a deck is not uniformly
    written by one model.
    """
    chain = settings.build_provider_chain
    if not chain:
        return raw_ai_provider()
    if len(chain) == 1:
        return provider_for(chain[0])
    return FailoverAIService(
        [provider_for(name) for name in chain],
        breaker_threshold=settings.ai_breaker_threshold,
        breaker_cooldown_seconds=settings.ai_breaker_cooldown_seconds,
        # Builds are not interactive and their models think for longer, so the
        # deadline is the sum of the gateways' own timeouts rather than a
        # spinner budget. A batch that stalls is retried by Celery.
        deadline_seconds=settings.ai_build_deadline_seconds,
    )


def grounded_build_provider() -> AIService:
    """The build pipeline's gateway, grounded when the dictionary is enabled."""
    provider = build_ai_provider()
    if not settings.dictionary_enabled:
        return provider
    return GroundedAIService(
        provider,
        dictionary_service(),
        translator=cast("SenseTranslator", provider),
        max_cards=MAX_LOOKUP_SUGGESTIONS,
        rewrite_definitions=settings.dictionary_rewrite_definitions,
    )


def build_model() -> str:
    """The build chain's primary model, for provenance on senses it writes."""
    chain = settings.build_provider_chain
    if not chain:
        return configured_model()
    return str(getattr(settings, f"{chain[0]}_model", ""))


def build_provider_name() -> str:
    """The build chain's primary gateway name, for provenance."""
    chain = settings.build_provider_chain
    return chain[0] if chain else settings.ai_provider


@lru_cache
def provider_for(name: str) -> AIService:
    """One configured OpenAI-protocol gateway, memoized per name.

    Memoized so each gateway keeps a single HTTP client and connection pool
    across requests. Keyed by name rather than by profile so that a chain naming
    the same gateway twice cannot open two pools onto it.
    """
    profile = settings.provider_profile(name)
    return PROVIDERS[profile.name](
        api_key=profile.api_key,
        model=profile.model,
        base_url=profile.base_url,
        timeout_seconds=profile.timeout_seconds,
        max_tokens=profile.max_tokens,
        extra_headers=profile.extra_headers,
    )


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
    """The *primary* gateway's model, used only as a provenance fallback.

    Under failover the gateway that answered may not be the configured one, so
    this is no longer the authoritative answer to "who wrote this card":
    ``LookupResult.provider``/``.model`` are, and the cache and lexicon prefer
    them. This remains for the paths that have no result to read it from — a
    story, an enrichment — and for the stub.
    """
    if settings.ai_provider == "anthropic":
        return settings.anthropic_model
    chain = settings.provider_chain
    if chain:
        return str(getattr(settings, f"{chain[0]}_model", ""))
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


def reset_provider_cache() -> None:
    """Drop every memoized gateway client. For tests and for ``run_async``.

    Each cached adapter holds an HTTP connection pool bound to the event loop
    that opened it, so a Celery task reusing one built by the previous task's
    loop fails the way ``app.tasks.runtime`` describes. Registered there.
    """
    provider_for.cache_clear()
    _anthropic_ai_service.cache_clear()
