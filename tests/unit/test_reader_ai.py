"""The reader's two AI methods, on every adapter and through the failover chain.

Mocked at the HTTP transport like the other adapter tests, so the SDK's own
request and response handling runs. What is pinned: the prompts reach the
model with the text tagged as data; an empty meaning is a malformed answer, not
a blank card; a translation keeps the source's line structure even when the
model does not; and the failover chain delegates both methods, failing over
on a gateway failure and nothing else.
"""

from __future__ import annotations

import json

import pytest

from app.application.ports.ai_service import (
    AIService,
    GeneratedStory,
    LearnerContext,
    LookupResult,
    MeaningSuggestion,
)
from app.application.ports.reader_ai import ContextualMeaning, PassageTranslationResult
from app.core.exceptions import (
    AllProvidersUnavailableError,
    ExternalServiceError,
    ValidationError,
)
from app.infrastructure.ai.failover_ai_service import FailoverAIService
from app.infrastructure.ai.reader_adapter_methods import match_layout
from app.infrastructure.ai.stub_ai_service import StubAIService
from tests.unit import test_anthropic_ai_service as anthropic_helpers
from tests.unit import test_avalai_ai_service as openai_helpers

LEARNER = LearnerContext(native_language="Persian")
SENSES = [
    MeaningSuggestion("بانک", "an organization that keeps and lends money", "", "Finance", "noun"),
    MeaningSuggestion("ساحل", "the land along the side of a river", "", "River", "noun"),
]
VERSE = "How doth the little crocodile\nImprove his shining tail,\nAnd pour the waters"


# ── match_layout ──────────────────────────────────────────────


def test_a_one_line_paragraph_stays_one_line() -> None:
    assert match_layout("One paragraph.", "  سطر اول\nسطر دوم \n") == "سطر اول سطر دوم"


def test_verse_loses_blank_lines_its_source_did_not_have() -> None:
    # Measured on avalai: the Alice verse came back with a stanza break added.
    translated = "یک\nدو\n\nسه"
    assert match_layout(VERSE, translated) == "یک\nدو\nسه"


def test_a_real_stanza_break_survives() -> None:
    assert match_layout("a\n\nb", "x\n\ny") == "x\n\ny"


# ── The OpenAI-protocol adapter (all four gateways) ───────────

MEANING = {
    "lemma": "stand",
    "part_of_speech": "verb",
    "context": "Position",
    "definition": "to be on your feet in an upright position",
    "native_meaning": "ایستادن",
}


async def test_meaning_in_context_sends_the_tagged_word_and_sentence() -> None:
    handler = openai_helpers._responder(openai_helpers._completion(MEANING))
    service = openai_helpers._service(handler)
    meaning = await service.meaning_in_context(
        "stood", "Nadia stood at the stop and counted the people.", LEARNER
    )
    assert (meaning.lemma, meaning.part_of_speech, meaning.context) == (
        "stand",
        "verb",
        "Position",
    )
    assert meaning.native_meaning == "ایستادن"
    assert meaning.provider == service.name and meaning.model == service.model
    request = handler.captured[0]
    assert "Persian" in request["messages"][0]["content"]
    user = request["messages"][1]["content"]
    assert "<word>stood</word>" in user
    assert "<sentence>Nadia stood at the stop and counted the people.</sentence>" in user
    assert request["response_format"]["json_schema"]["name"] == "contextual_meaning"


async def test_an_empty_meaning_is_a_malformed_answer() -> None:
    handler = openai_helpers._responder(
        openai_helpers._completion({**MEANING, "definition": "", "native_meaning": ""})
    )
    with pytest.raises(ExternalServiceError):
        await openai_helpers._service(handler).meaning_in_context("stood", "She stood.", LEARNER)


async def test_a_translation_is_returned_with_the_source_layout() -> None:
    handler = openai_helpers._responder(
        openai_helpers._completion({"translation": "یک\n\nدو\nسه\n"})
    )
    result = await openai_helpers._service(handler).translate_passage(
        VERSE, "Persian", preceding="The previous paragraph.", book_title="Alice"
    )
    assert result.translation == "یک\nدو\nسه"
    request = handler.captured[0]
    assert "Persian" in request["messages"][0]["content"]
    user = request["messages"][1]["content"]
    assert "<passage>" in user and "<preceding>" in user and "Alice" in user


async def test_a_failing_gateway_is_an_external_service_error() -> None:
    handler = openai_helpers._responder({}, status=500)
    with pytest.raises(ExternalServiceError):
        await openai_helpers._service(handler).translate_passage("Text.", "Persian")


# ── The Anthropic adapter ─────────────────────────────────────


async def test_the_anthropic_adapter_answers_both_questions() -> None:
    handler = anthropic_helpers._responder(
        anthropic_helpers._message(MEANING),
        anthropic_helpers._message({"translation": "  متن ترجمه‌شده  "}),
    )
    service = anthropic_helpers._service(handler)
    meaning = await service.meaning_in_context("stood", "She stood.", LEARNER)
    result = await service.translate_passage("One paragraph.", "Persian")
    assert (meaning.lemma, meaning.provider) == ("stand", "anthropic")
    assert result.translation == "متن ترجمه‌شده"
    assert "<sentence>She stood.</sentence>" in json.dumps(handler.captured[0], ensure_ascii=False)


# ── The stub ──────────────────────────────────────────────────


async def test_the_stub_answers_deterministically_and_offline() -> None:
    stub = StubAIService()
    meaning = await stub.meaning_in_context("Banks", "They sat on the bank.", LEARNER)
    assert meaning.lemma == "banks" and "They sat on the" in meaning.definition
    result = await stub.translate_passage("Hello.", "Persian")
    assert result.translation == "[Persian] Hello."


# ── Failover ──────────────────────────────────────────────────


class ReaderGateway(AIService):
    """Implements only what the reader calls, failing as the test chooses."""

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
        raise NotImplementedError

    async def generate_story(self, words: list[str], learner: LearnerContext) -> GeneratedStory:
        raise NotImplementedError

    async def meaning_in_context(
        self, word: str, sentence: str, learner: LearnerContext
    ) -> ContextualMeaning:
        self._answer()
        return ContextualMeaning(word, "noun", "Ctx", "a definition", "معنی", provider=self.name)

    async def translate_passage(
        self, text: str, target_language: str, preceding: str = "", book_title: str = ""
    ) -> PassageTranslationResult:
        self._answer()
        return PassageTranslationResult(f"{target_language}|{preceding}|{text}", self.name)


def _chain(*gateways: AIService) -> FailoverAIService:
    return FailoverAIService(list(gateways), deadline_seconds=60)


async def test_both_reader_methods_fail_over_to_the_next_gateway() -> None:
    down = ReaderGateway("down", fail_with=ExternalServiceError("stalled"))
    up = ReaderGateway("up")
    chain = _chain(down, up)
    answer = await chain.meaning_in_context("bank", "s", LEARNER)
    result = await chain.translate_passage("text", "Persian", "before", "title")
    assert (answer.provider, result.provider) == ("up", "up")
    # Every argument reaches the gateway, in order: the delegation is positional.
    assert result.translation == "Persian|before|text"


async def test_a_validation_error_is_not_retried_elsewhere() -> None:
    bad = ReaderGateway("bad", fail_with=ValidationError("about the input"))
    spare = ReaderGateway("spare")
    with pytest.raises(ValidationError):
        await _chain(bad, spare).translate_passage("text", "Persian")
    assert spare.calls == 0


async def test_a_gateway_without_the_method_is_skipped_like_a_failed_one() -> None:
    class LookupOnly(AIService):
        name = "old"
        timeout_seconds = 30.0

        async def look_up_meanings(self, term: str, learner: LearnerContext) -> LookupResult:
            raise NotImplementedError

        async def generate_story(self, words: list[str], learner: LearnerContext) -> GeneratedStory:
            raise NotImplementedError

    up = ReaderGateway("up")
    answer = await _chain(LookupOnly(), up).meaning_in_context("bank", "s", LEARNER)
    assert answer.provider == "up"


async def test_every_gateway_down_is_all_providers_unavailable() -> None:
    chain = _chain(
        ReaderGateway("a", fail_with=ExternalServiceError("x")),
        ReaderGateway("b", fail_with=ExternalServiceError("y")),
    )
    with pytest.raises(AllProvidersUnavailableError):
        await chain.translate_passage("text", "Persian")
