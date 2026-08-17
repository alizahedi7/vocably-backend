"""Try the next gateway when this one fails.

One gateway is a single point of failure, and the failure is not hypothetical: a
seven-minute upstream stall on 2026-08-17 turned every lookup in that window into
a 502 because there was nothing to fall back to. This wrapper makes the fleet the
unit of availability instead of the gateway.

**Where it sits is the whole design.** It goes at the *bottom* of the chain,
inside ``raw_ai_provider()``::

    cache → lexicon → grounded → FAILOVER → [primary, fallback, …]

Not above the cache. Below it, one cached entry and one lexeme serve every
learner no matter which gateway wrote them, and ``CachingAIService`` and
``LexiconAIService`` never learn that failover exists. Above it, a failover would
route around both and re-buy the corpus at the moment the app can least afford
it. It also means the enricher and translator paths — which are handed
``raw_ai_provider()`` directly — get failover without knowing about it.

**It delegates five methods, not two.** ``look_up_meanings`` and
``generate_story`` are the port; ``translate_only``, ``translate_senses`` and
``enrich_senses`` are the structural protocols that ``GroundedAIService`` and
``LexiconAIService`` cast this object to. Miss one and grounding silently loses
failover at runtime with no type error to catch it.

**It trips on :class:`ExternalServiceError` and nothing else.** The adapters
already funnel every transport error, status code, content filter and schema
failure into it, so that one type means "this gateway did not answer".
``ValidationError`` is about the learner's input and would fail identically
everywhere — retrying it on a second gateway spends money to produce the same
422.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Final

from app.application.ports.ai_service import (
    AIService,
    GeneratedStory,
    LearnerContext,
    LookupResult,
    MeaningSuggestion,
)
from app.core.exceptions import AllProvidersUnavailableError, ExternalServiceError
from app.core.logging import get_logger

logger = get_logger("vocably.ai.failover")

#: Consecutive failures before a gateway is taken out of rotation. Low, because
#: the cost of being wrong is one extra probe per cooldown and the cost of being
#: slow is every learner paying that gateway's timeout first.
DEFAULT_BREAKER_THRESHOLD: Final = 3
#: How long a tripped gateway is skipped before one request is allowed to test
#: it. Long enough to ride out the stall that tripped it, short enough that a
#: recovered primary is back in service without a deploy.
DEFAULT_BREAKER_COOLDOWN_SECONDS: Final = 60.0
#: Ceiling on the whole call, across every gateway tried. Without it, three
#: gateways at a 30 s timeout stack into 90 s of spinner before the 502 — worse
#: for the learner than the single-gateway failure this exists to fix.
DEFAULT_DEADLINE_SECONDS: Final = 45.0


def _name_of(service: AIService) -> str:
    """The gateway's configured name, duck-typed as everything else here is."""
    return str(getattr(service, "name", type(service).__name__))


class CircuitBreaker:
    """Per-gateway memory of recent failure, so a dead one is skipped, not retried.

    Closed → ``threshold`` consecutive failures → **open** for ``cooldown`` →
    **half-open**, where a single request decides: success closes it, failure
    reopens it for another cooldown.

    **Never load-bearing**, the same rule
    :mod:`~app.infrastructure.ai.single_flight` follows. It is in-process and
    unsynchronised, so the API and each worker hold their own view and two
    concurrent requests can both slip through half-open. Both outcomes cost one
    redundant call. Correctness lives in the try-the-next-one loop below, which
    works identically with every breaker permanently closed.
    """

    def __init__(
        self,
        *,
        threshold: int = DEFAULT_BREAKER_THRESHOLD,
        cooldown_seconds: float = DEFAULT_BREAKER_COOLDOWN_SECONDS,
    ) -> None:
        self._threshold = threshold
        self._cooldown = cooldown_seconds
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        """True while this gateway should be skipped outright."""
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self._cooldown:
            # Half-open: leave the failure count where it is, so a failed probe
            # reopens immediately rather than needing another full threshold.
            self._opened_at = None
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = time.monotonic()


class FailoverAIService(AIService):
    """Calls each gateway in order until one answers."""

    def __init__(
        self,
        providers: Sequence[AIService],
        *,
        breaker_threshold: int = DEFAULT_BREAKER_THRESHOLD,
        breaker_cooldown_seconds: float = DEFAULT_BREAKER_COOLDOWN_SECONDS,
        deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    ) -> None:
        if not providers:
            raise ValueError("FailoverAIService needs at least one provider.")
        self._providers = list(providers)
        self._deadline = deadline_seconds
        self._breakers = {
            id(p): CircuitBreaker(
                threshold=breaker_threshold,
                cooldown_seconds=breaker_cooldown_seconds,
            )
            for p in self._providers
        }

    # ── The port ──────────────────────────────────────────────

    async def look_up_meanings(self, term: str, learner: LearnerContext) -> LookupResult:
        return await self._attempt(
            "look_up_meanings",
            lambda p: p.look_up_meanings(term, learner),
        )

    async def generate_story(self, words: list[str], learner: LearnerContext) -> GeneratedStory:
        return await self._attempt(
            "generate_story",
            lambda p: p.generate_story(words, learner),
        )

    # ── The structural protocols ──────────────────────────────
    # SenseTranslator and SenseEnricher. Delegated by name rather than by
    # inheritance for the same reason the adapters satisfy them structurally:
    # there is no second base class to drag in. ``getattr`` because a provider
    # need not implement them — the stub in a test may not — and a chain whose
    # first gateway lacks the method should move on rather than crash.

    async def translate_only(
        self,
        term: str,
        entry_text: str,
        learner: LearnerContext,
        max_cards: int,
    ) -> list[tuple[int, str, str]]:
        return await self._attempt(
            "translate_only",
            lambda p: self._delegate(p, "translate_only", term, entry_text, learner, max_cards),
        )

    async def translate_senses(
        self,
        term: str,
        entry_text: str,
        learner: LearnerContext,
        max_cards: int,
    ) -> list[MeaningSuggestion]:
        return await self._attempt(
            "translate_senses",
            lambda p: self._delegate(p, "translate_senses", term, entry_text, learner, max_cards),
        )

    async def enrich_senses(
        self,
        term: str,
        known: list[MeaningSuggestion],
        wanted: str,
        learner: LearnerContext,
        max_new: int,
    ) -> list[MeaningSuggestion]:
        return await self._attempt(
            "enrich_senses",
            lambda p: self._delegate(p, "enrich_senses", term, known, wanted, learner, max_new),
        )

    # ── The loop ──────────────────────────────────────────────

    @staticmethod
    def _delegate[T](provider: AIService, method: str, *args: object) -> Awaitable[T]:
        """Return the coroutine for one optional method, or fail like a gateway would.

        Deliberately not ``async``: it returns the awaitable rather than
        awaiting it, so the return type carries ``T`` through to ``_attempt``
        and each caller keeps its real signature. The raise still lands inside
        ``_attempt``'s ``try``, because the lambda is only called there — so a
        gateway that does not implement the method is skipped exactly as one
        that failed.
        """
        call = getattr(provider, method, None)
        if call is None:
            raise ExternalServiceError(f"Provider does not implement {method}.")
        awaitable: Awaitable[T] = call(*args)
        return awaitable

    async def _attempt[T](self, operation: str, call: Callable[[AIService], Awaitable[T]]) -> T:
        """Run ``call`` against each gateway in turn until one succeeds.

        Returns the first success. Raises :class:`AllProvidersUnavailableError`
        only once every gateway has been tried, skipped or timed out of budget —
        never earlier, because that exception halts a deck build.
        """
        started = time.monotonic()
        attempted = 0
        last_error: ExternalServiceError | None = None

        for index, provider in enumerate(self._providers):
            name = _name_of(provider)
            breaker = self._breakers[id(provider)]

            if breaker.is_open:
                logger.info("skipping %s for %s: circuit open", name, operation)
                continue

            # Always let the first attempt run: a deadline shorter than one
            # gateway's timeout is a misconfiguration, and answering it by
            # making zero calls would turn a slow lookup into a total outage.
            if attempted and not self._within_budget(provider, started):
                logger.warning(
                    "skipping %s for %s: %.1fs budget spent", name, operation, self._deadline
                )
                continue

            attempted += 1
            try:
                result = await call(provider)
            except ExternalServiceError as exc:
                breaker.record_failure()
                last_error = exc
                remaining = self._providers[index + 1 :]
                logger.warning(
                    "%s failed %s (%s); %s",
                    name,
                    operation,
                    type(exc).__name__,
                    (
                        f"falling over to {_name_of(remaining[0])}"
                        if remaining
                        else "no gateways left"
                    ),
                )
                continue

            breaker.record_success()
            if index:
                # Only worth a line when it was not the primary: this one costs
                # money and may change the wording a learner reads.
                logger.warning("%s served %s after %d failure(s)", name, operation, index)
            return result

        logger.error(
            "every gateway failed %s (%d tried of %d configured)",
            operation,
            attempted,
            len(self._providers),
        )
        raise AllProvidersUnavailableError() from last_error

    def _within_budget(self, provider: AIService, started: float) -> bool:
        """Whether ``provider`` can finish inside what is left of the deadline.

        Measured against the gateway's own timeout rather than an average, so a
        slow fallback is skipped before it is started instead of blowing the
        budget halfway through.
        """
        spent = time.monotonic() - started
        needs = float(getattr(provider, "timeout_seconds", 0.0) or 0.0)
        return spent + needs <= self._deadline
