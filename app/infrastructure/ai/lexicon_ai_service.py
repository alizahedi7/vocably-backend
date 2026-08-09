"""The lexicon as a layer of the lookup pipeline.

A decorator over :class:`AIService`, like :class:`CachingAIService` above it and
:class:`GroundedAIService` below, so the application layer keeps calling one port
and never learns that durable shared knowledge exists.

Composition order, and the reason for it::

    AIStudioService
      └─ CachingAIService      # request-shaped, keyed by prompt version
          └─ LexiconAIService  # term-shaped, durable, survives a prompt bump
              └─ GroundedAIService
                  └─ provider

The cache sits **outside**, so a repeat lookup costs one indexed read and never
touches this layer. The lexicon sits **inside**, so it answers the requests the
cache cannot — and the day ``PROMPT_VERSION`` is bumped, that is *every* request.
Without this layer a prompt edit re-buys the entire corpus; with it, a prompt
edit costs nothing for words already known. That is the whole point of the
ordering, and swapping the two would quietly undo it.

Three properties, all of them inherited rather than invented:

* **Never load-bearing.** A read that fails is a miss; a write that fails is
  logged and dropped. A learner already waiting on a paid-for answer must never
  get a 500 because a bookkeeping write failed.
* **Impersonal.** Nothing here is keyed by a user, and a learner's later edits to
  their card never come back.
* **Append-only.** Stored senses are never rewritten by this path — a hit that
  covers only part of what a provider would say still tops up, because
  ``sense_key`` collisions are skipped rather than overwritten.
"""

from __future__ import annotations

from typing import Protocol

from app.application.ports.ai_service import (
    AIService,
    GeneratedStory,
    LearnerContext,
    LookupResult,
    MeaningSuggestion,
)
from app.application.ports.lookup_cache import CacheAgeBucket, normalize_lookup_input
from app.application.services.lexicon_service import LexiconService
from app.core.logging import get_logger
from app.domain.enums import SenseSource
from app.infrastructure.ai.single_flight import SingleFlight

logger = get_logger("vocably.ai.lexicon")


class SenseEnricher(Protocol):
    """Asks a provider for the senses of a word we do *not* already have.

    A structural protocol rather than a base class, exactly like
    :class:`~app.infrastructure.ai.grounded_ai_service.SenseTranslator`: an
    adapter satisfies it by having the method, so there is no second inheritance
    chain alongside :class:`AIService` and no import from the adapters back here.
    """

    async def enrich_senses(
        self,
        term: str,
        known: list[MeaningSuggestion],
        wanted: str,
        learner: LearnerContext,
        max_new: int,
    ) -> list[MeaningSuggestion]:
        """Return senses of ``term`` that are **not** among ``known``.

        ``wanted`` describes the sense the caller is missing, in the template
        author's own words. Implementations return an empty list rather than
        inventing a sense to satisfy the request.
        """
        ...


class LexiconAIService(AIService):
    def __init__(
        self,
        inner: AIService,
        lexicon: LexiconService,
        *,
        single_flight: SingleFlight | None = None,
        register_for: bool = True,
    ) -> None:
        self._inner = inner
        self._lexicon = lexicon
        self._single_flight = single_flight
        #: Whether this layer writes as well as reads. Off makes it a pure cache
        #: read — used by nothing today, kept because the read and the write are
        #: separately debuggable and conflating them once cost an afternoon.
        self._writes = register_for

    async def look_up_meanings(self, term: str, learner: LearnerContext) -> LookupResult:
        register = _register_for(learner)

        hit = await self._read(term, learner, register)
        if hit is not None:
            logger.info("lexicon hit (senses=%d)", len(hit.suggestions))
            return hit

        key = self._flight_key(term, learner, register)
        holder = self._single_flight
        owns = True
        if holder is not None:
            owns = await holder.acquire(key)
            if not owns:
                # Someone else is already paying for this word. Give them a
                # bounded moment, then generate anyway rather than make this
                # learner wait on a call we cannot see.
                waited = await holder.wait_for_result(
                    lambda: self._read(term, learner, register),
                )
                if isinstance(waited, LookupResult):
                    logger.info("lexicon hit after single-flight wait")
                    return waited

        try:
            result = await self._inner.look_up_meanings(term, learner)
        finally:
            if holder is not None and owns:
                await holder.release(key)

        if self._writes:
            await self._write(result, learner, register)
        return result

    async def generate_story(self, words: list[str], learner: LearnerContext) -> GeneratedStory:
        """Passed straight through — a story is prose, not a fact about a word."""
        return await self._inner.generate_story(words, learner)

    # ── Helpers ───────────────────────────────────────────────

    async def _read(self, term: str, learner: LearnerContext, register: str) -> LookupResult | None:
        try:
            lexeme = await self._lexicon.find(term)
            if lexeme is None:
                return None
            return await self._lexicon.as_lookup_result(
                lexeme,
                native_language=learner.native_language,
                register=register,
            )
        except Exception:
            # Never log the raw term: it is learner text.
            logger.warning("lexicon read failed; falling through", exc_info=True)
            return None

    async def _write(self, result: LookupResult, learner: LearnerContext, register: str) -> None:
        try:
            await self._lexicon.record(
                result,
                learner,
                source=SenseSource.LOOKUP,
                register=register,
            )
        except Exception:
            logger.warning("lexicon write failed; result served unrecorded", exc_info=True)

    @staticmethod
    def _flight_key(term: str, learner: LearnerContext, register: str) -> str:
        return ":".join(
            (
                normalize_lookup_input(term),
                learner.native_language.casefold(),
                register,
            )
        )


def _register_for(learner: LearnerContext) -> str:
    """Which audience's wording this learner gets.

    The same three buckets the lookup cache collapses ``AgeRange`` to, and for
    the same reason: a 25-34 learner and a 45-54 one get identical text, so
    storing eight variants of every sense would shard the lexicon for nothing.
    """
    return CacheAgeBucket.from_age_range(learner.age_range).value
