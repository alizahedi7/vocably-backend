"""A cache in front of any :class:`AIService` adapter.

A decorator over the port rather than logic inside ``AIStudioService``, for three
reasons: it works identically for every provider, the business rules stay
ignorant that a cache exists at all, and the test suite's ``StubAIService`` can
be wired with or without it by changing one dependency.

**The cache is never in the correctness path.** Every interaction with it is
best-effort: a read that fails becomes a miss, a write that fails is logged and
dropped. A cache fault must never turn an answer the learner is already waiting
on — and that has already been paid for — into a 500.

The write is awaited inline rather than dispatched to a background task. It is a
single indexed insert against a connection this request already holds, which is
noise beside the multi-second provider call it follows; backgrounding it would
mean outliving the request-scoped session that FastAPI is about to close.
"""

from __future__ import annotations

from app.application.ports.ai_service import (
    AIService,
    GeneratedStory,
    LearnerContext,
    LookupResult,
    LookupStatus,
)
from app.application.ports.lookup_cache import (
    LookupCacheRepository,
    build_lookup_cache_key,
)
from app.core.logging import get_logger
from app.infrastructure.ai.prompts import PROMPT_VERSION

logger = get_logger("vocably.ai.cache")


class CachingAIService(AIService):
    def __init__(
        self,
        inner: AIService,
        cache: LookupCacheRepository,
        *,
        unsupported_ttl_seconds: int,
        provider: str = "",
        model: str = "",
        prompt_version: int = PROMPT_VERSION,
    ) -> None:
        self._inner = inner
        self._cache = cache
        self._unsupported_ttl_seconds = unsupported_ttl_seconds
        self._provider = provider
        self._model = model
        # Overridable because the pipeline below is not always the generative
        # one: with dictionary grounding on, cards are written by a different
        # prompt and must not collide with cards written without it. Callers
        # pass a version that identifies the whole pipeline, not just this
        # module's prompt — see ``deps._effective_prompt_version``.
        self._prompt_version = prompt_version

    async def look_up_meanings(self, term: str, learner: LearnerContext) -> LookupResult:
        key = build_lookup_cache_key(term, learner, self._prompt_version)

        try:
            if hit := await self._cache.get(key):
                logger.info(
                    "lookup cache hit (lang=%s age=%s v=%s)",
                    key.native_language,
                    key.age_bucket.value,
                    key.prompt_version,
                )
                return hit
        except Exception:
            # Never log the key's input — it is raw learner text.
            logger.warning("lookup cache read failed; falling through", exc_info=True)

        result = await self._inner.look_up_meanings(term, learner)

        try:
            await self._cache.put(
                key,
                result,
                # Unintelligible input is the one answer worth forgetting: it
                # costs nothing to re-ask later, and keeping it forever would
                # let anyone grow the table with garbage.
                alias_ttl_seconds=(
                    self._unsupported_ttl_seconds
                    if result.status is LookupStatus.UNSUPPORTED
                    else None
                ),
                # What answered, not what is configured. Under failover the two
                # differ exactly when a fallback served the card, and that is
                # the case where recording the configured primary would file a
                # sense under a model that never saw the word.
                provider=result.provider or self._provider,
                model=result.model or self._model,
            )
        except Exception:
            logger.warning("lookup cache write failed; result served uncached", exc_info=True)

        logger.info(
            "lookup cache miss (lang=%s age=%s v=%s status=%s)",
            key.native_language,
            key.age_bucket.value,
            key.prompt_version,
            result.status.value,
        )
        return result

    async def generate_story(self, words: list[str], learner: LearnerContext) -> GeneratedStory:
        """Passed straight through — a story is per-learner by construction.

        Its word set comes from one person's Leitner boxes, so no two requests
        share a key worth caching.
        """
        return await self._inner.generate_story(words, learner)
