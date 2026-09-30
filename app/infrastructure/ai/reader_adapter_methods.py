"""The two reader methods every provider adapter grows, written once.

A mixin over the adapters' existing ``_complete`` transport, so
``OpenAICompatibleAIService`` — and therefore all four gateways — gains
``meaning_in_context`` and ``translate_passage`` by inheriting it. The Anthropic
adapter's ``_complete`` takes no ``schema_name``; it gets the same two bodies
minus that argument. Every failure leaves as :class:`ExternalServiceError`,
which is what ``FailoverAIService`` reads to try the next gateway.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from app.application.ports.ai_service import LearnerContext
from app.application.ports.reader_ai import ContextualMeaning, PassageTranslationResult
from app.infrastructure.ai.payloads import ContextualMeaningPayload, PassagePayload
from app.infrastructure.ai.reader_prompts import (
    CONTEXTUAL_MEANING_JSON_SCHEMA,
    PASSAGE_JSON_SCHEMA,
    contextual_meaning_system_prompt,
    contextual_meaning_user_prompt,
    passage_system_prompt,
    passage_user_prompt,
)


class _Completes(Protocol):
    """What the mixin needs from its host: the adapter's identity and transport."""

    name: str

    @property
    def model(self) -> str: ...

    async def _complete[T: BaseModel](
        self,
        system: str,
        user: str,
        schema: dict[str, object],
        schema_name: str,
        model_type: type[T],
    ) -> T: ...


class ReaderAdapterMixin:
    async def meaning_in_context(
        self: _Completes,
        word: str,
        sentence: str,
        learner: LearnerContext,
    ) -> ContextualMeaning:
        payload = await self._complete(
            contextual_meaning_system_prompt(native_language=learner.native_language),
            contextual_meaning_user_prompt(word, sentence),
            CONTEXTUAL_MEANING_JSON_SCHEMA,
            "contextual_meaning",
            ContextualMeaningPayload,
        )
        return ContextualMeaning(
            lemma=payload.lemma.strip(),
            part_of_speech=payload.part_of_speech.strip(),
            context=payload.context.strip(),
            definition=payload.definition.strip(),
            native_meaning=payload.native_meaning.strip(),
            provider=self.name,
            model=self.model,
        )

    async def translate_passage(
        self: _Completes,
        text: str,
        target_language: str,
        preceding: str = "",
        book_title: str = "",
    ) -> PassageTranslationResult:
        payload = await self._complete(
            passage_system_prompt(target_language=target_language),
            passage_user_prompt(text, preceding=preceding, book_title=book_title),
            PASSAGE_JSON_SCHEMA,
            "passage_translation",
            PassagePayload,
        )
        return PassageTranslationResult(
            translation=match_layout(text, payload.translation),
            provider=self.name,
            model=self.model,
        )


def match_layout(source: str, translated: str) -> str:
    """Give the translation the source's line structure, deterministically.

    The prompt asks for it and models mostly comply, but measured on the Alice
    verse, one gateway added a blank stanza line (8 lines became 9) where the
    other did not. The client shows the two side by side line by line, so this
    is enforced here rather than hoped for: a one-line paragraph stays one line,
    and blank lines survive only if the source had them.
    """
    text = translated.strip()
    if "\n" not in source:
        return " ".join(text.split())
    lines = [line.strip() for line in text.split("\n")]
    if "\n\n" not in source:
        lines = [line for line in lines if line]
    return "\n".join(lines)
