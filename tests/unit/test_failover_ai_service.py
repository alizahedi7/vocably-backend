"""What the failover wrapper must do, and what it must refuse to do.

The rules being pinned here are the ones that are cheap to break silently: a
lookup that fails over on a *validation* error spends money to produce the same
422; a chain that stops raising ``ExternalServiceError`` stops halting a deck
build; and a wrapper that forgets the structural protocols loses failover on the
grounded and enrichment paths with no type error to catch it.
"""

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
from app.core.exceptions import (
    AllProvidersUnavailableError,
    ExternalServiceError,
    ValidationError,
)
from app.infrastructure.ai.failover_ai_service import CircuitBreaker, FailoverAIService

LEARNER = LearnerContext(native_language="Persian")


class FakeProvider(AIService):
    """A gateway that answers, or fails in a way of the test's choosing."""

    def __init__(self, name: str, *, fail_with: Exception | None = None) -> None:
        self.name = name
        self.timeout_seconds = 30.0
        self._fail_with = fail_with
        self.calls = 0

    def _answer(self) -> None:
        self.calls += 1
        if self._fail_with is not None:
            raise self._fail_with

    async def look_up_meanings(self, term: str, learner: LearnerContext) -> LookupResult:
        self._answer()
        return LookupResult(
            term=term,
            suggestions=[MeaningSuggestion(native_meaning="معنی", definition="d")],
            status=LookupStatus.OK,
            provider=self.name,
            model=f"{self.name}-model",
        )

    async def generate_story(self, words: list[str], learner: LearnerContext) -> GeneratedStory:
        self._answer()
        return GeneratedStory(text=f"story from {self.name}", words_used=words)

    async def translate_only(
        self, term: str, entry_text: str, learner: LearnerContext, max_cards: int
    ) -> list[tuple[int, str, str]]:
        self._answer()
        return [(0, self.name, "ctx")]

    async def translate_senses(
        self, term: str, entry_text: str, learner: LearnerContext, max_cards: int
    ) -> list[MeaningSuggestion]:
        self._answer()
        return [MeaningSuggestion(native_meaning=self.name)]

    async def enrich_senses(
        self,
        term: str,
        known: list[MeaningSuggestion],
        wanted: str,
        learner: LearnerContext,
        max_new: int,
    ) -> list[MeaningSuggestion]:
        self._answer()
        return [MeaningSuggestion(native_meaning=self.name)]


def _down(name: str) -> FakeProvider:
    return FakeProvider(name, fail_with=ExternalServiceError("gateway is down"))


# ── The basic contract ────────────────────────────────────────


async def test_a_healthy_primary_is_the_only_one_called() -> None:
    primary, fallback = FakeProvider("primary"), FakeProvider("fallback")
    service = FailoverAIService([primary, fallback])

    result = await service.look_up_meanings("run", LEARNER)

    assert result.provider == "primary"
    assert primary.calls == 1
    assert fallback.calls == 0


async def test_a_failed_primary_falls_over_to_the_next_gateway() -> None:
    primary, fallback = _down("primary"), FakeProvider("fallback")
    service = FailoverAIService([primary, fallback])

    result = await service.look_up_meanings("run", LEARNER)

    assert result.suggestions, "the learner must still get their card"
    assert fallback.calls == 1


async def test_the_result_names_the_gateway_that_actually_answered() -> None:
    """Otherwise a card written by the fallback is filed under the primary,
    and ``/admin/ai-feedback`` blames the wrong model for a bad sense."""
    service = FailoverAIService([_down("primary"), FakeProvider("fallback")])

    result = await service.look_up_meanings("run", LEARNER)

    assert result.provider == "fallback"
    assert result.model == "fallback-model"


async def test_every_gateway_failing_raises_all_providers_unavailable() -> None:
    service = FailoverAIService([_down("a"), _down("b")])

    with pytest.raises(AllProvidersUnavailableError):
        await service.look_up_meanings("run", LEARNER)


async def test_that_error_is_still_an_external_service_error() -> None:
    """Two things depend on this and neither knows the subclass exists:
    ``app.api.errors`` maps it to 502, and ``DeckBuildService`` halts a build."""
    service = FailoverAIService([_down("a")])

    with pytest.raises(ExternalServiceError):
        await service.look_up_meanings("run", LEARNER)


async def test_the_last_gateways_failure_is_kept_as_the_cause() -> None:
    """So an operator reading the traceback sees why the fleet went down."""
    service = FailoverAIService([_down("a"), _down("b")])

    with pytest.raises(AllProvidersUnavailableError) as caught:
        await service.look_up_meanings("run", LEARNER)

    assert isinstance(caught.value.__cause__, ExternalServiceError)


# ── What must NOT fail over ───────────────────────────────────


async def test_a_validation_error_propagates_without_trying_the_next_gateway() -> None:
    """The learner's input is bad. It will be bad on every gateway, so retrying
    it elsewhere spends money to produce the same 422."""
    primary = FakeProvider("primary", fail_with=ValidationError("term is too long"))
    fallback = FakeProvider("fallback")
    service = FailoverAIService([primary, fallback])

    with pytest.raises(ValidationError):
        await service.look_up_meanings("…", LEARNER)

    assert fallback.calls == 0


async def test_an_unexpected_exception_is_not_swallowed() -> None:
    """A bug in an adapter must surface as a bug, not as a quiet failover."""
    primary = FakeProvider("primary", fail_with=RuntimeError("boom"))
    fallback = FakeProvider("fallback")
    service = FailoverAIService([primary, fallback])

    with pytest.raises(RuntimeError):
        await service.look_up_meanings("run", LEARNER)

    assert fallback.calls == 0


# ── The structural protocols ──────────────────────────────────


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("generate_story", (["a", "b", "c"], LEARNER)),
        ("translate_only", ("run", "entry", LEARNER, 4)),
        ("translate_senses", ("run", "entry", LEARNER, 4)),
        ("enrich_senses", ("run", [], "a sense", LEARNER, 2)),
    ],
)
async def test_every_delegated_method_fails_over(method: str, args: tuple[object, ...]) -> None:
    """Miss one of these and grounding or enrichment silently loses failover."""
    primary, fallback = _down("primary"), FakeProvider("fallback")
    service = FailoverAIService([primary, fallback])

    result = await getattr(service, method)(*args)

    assert result
    assert fallback.calls == 1


async def test_a_gateway_missing_an_optional_method_is_skipped_not_crashed() -> None:
    class LookupOnly(AIService):
        name = "lookup-only"
        timeout_seconds = 30.0

        async def look_up_meanings(self, term: str, learner: LearnerContext) -> LookupResult:
            return LookupResult(term=term, suggestions=[])

        async def generate_story(self, words: list[str], learner: LearnerContext) -> GeneratedStory:
            return GeneratedStory(text="", words_used=[])

    fallback = FakeProvider("fallback")
    service = FailoverAIService([LookupOnly(), fallback])

    assert await service.enrich_senses("run", [], "a sense", LEARNER, 2)
    assert fallback.calls == 1


# ── The circuit breaker ───────────────────────────────────────


async def test_a_gateway_is_skipped_once_its_breaker_trips() -> None:
    primary, fallback = _down("primary"), FakeProvider("fallback")
    service = FailoverAIService([primary, fallback], breaker_threshold=2)

    for _ in range(3):
        await service.look_up_meanings("run", LEARNER)

    # Two failures trip it; the third lookup skips the primary entirely.
    assert primary.calls == 2
    assert fallback.calls == 3


async def test_the_breaker_reopens_a_gateway_after_its_cooldown() -> None:
    breaker = CircuitBreaker(threshold=1, cooldown_seconds=0.0)
    breaker.record_failure()

    # A zero cooldown is immediately half-open, which is the same code path a
    # real cooldown reaches when it elapses.
    assert breaker.is_open is False


async def test_a_failed_probe_reopens_without_needing_the_full_threshold_again() -> None:
    """Half-open keeps the failure count. A gateway that is still down must not
    get ``threshold`` free requests every cooldown — that is most of the traffic
    the breaker exists to keep away from it."""
    breaker = CircuitBreaker(threshold=2, cooldown_seconds=0.0)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.is_open is False, "a zero cooldown is immediately half-open"

    # One more failure — not two — puts it back in the open state.
    breaker.record_failure()
    assert breaker._failures > breaker._threshold
    assert breaker._opened_at is not None


async def test_success_closes_the_breaker() -> None:
    breaker = CircuitBreaker(threshold=2, cooldown_seconds=999.0)
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()

    assert breaker.is_open is False, "the count must reset, not accumulate across successes"


async def test_a_recovered_primary_is_used_again() -> None:
    """The breaker must not pin traffic to the fallback until someone deploys."""
    primary = FakeProvider("primary", fail_with=ExternalServiceError("down"))
    service = FailoverAIService(
        [primary, FakeProvider("fallback")], breaker_threshold=1, breaker_cooldown_seconds=0.0
    )

    await service.look_up_meanings("run", LEARNER)
    primary._fail_with = None
    result = await service.look_up_meanings("run", LEARNER)

    assert result.provider == "primary"


# ── The deadline ──────────────────────────────────────────────


async def test_a_slow_fallback_is_skipped_when_the_budget_cannot_cover_it() -> None:
    """Skipped *before* it is started, so the deadline is a ceiling rather than
    something noticed halfway through blowing it."""
    primary, fallback = _down("primary"), FakeProvider("fallback")
    fallback.timeout_seconds = 300.0
    service = FailoverAIService([primary, fallback], deadline_seconds=10.0)

    with pytest.raises(AllProvidersUnavailableError):
        await service.look_up_meanings("run", LEARNER)

    assert fallback.calls == 0


async def test_the_first_gateway_is_always_tried_however_small_the_deadline() -> None:
    """A deadline below one gateway's timeout is a misconfiguration. Answering it
    by making zero calls would turn a slow lookup into a total outage."""
    primary = FakeProvider("primary")
    service = FailoverAIService([primary], deadline_seconds=0.0)

    assert await service.look_up_meanings("run", LEARNER)
    assert primary.calls == 1


# ── Construction ──────────────────────────────────────────────


def test_an_empty_chain_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one provider"):
        FailoverAIService([])
