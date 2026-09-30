"""Ports: the two AI capabilities the reader needs beyond ``AIService``.

Structural protocols, exactly like ``SenseTranslator`` and ``SenseEnricher``:
every provider adapter grows these two methods, ``FailoverAIService`` delegates
them, and the reader service is handed ``raw_ai_provider()`` cast to each.
Miss one in the failover delegation and the reader loses failover at runtime
with no type error to say so — the rule ``CLAUDE.md`` already states for the
other five.

The meaning-in-context question deliberately takes **no sense list**. The
first design asked the model to pick an index from the stored senses, which
meant the lookup had to finish first: a cold word cost two model calls in a
row. Asking "what does this word mean here?" instead needs only the sentence,
so it runs alongside the lookup, and it still answers when the lexicon holds
no sense for this use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.application.ports.ai_service import LearnerContext


@dataclass(frozen=True, slots=True)
class ContextualMeaning:
    """What a word means in one sentence, and nothing about its other senses.

    Independent of the sense list on purpose: it can be asked the moment the
    tap arrives, in parallel with the lookup, and it answers even when the
    lexicon holds no sense for this use. ``lemma`` is the dictionary form as
    used here — "stood" → "stand", "gave up" → "give up" — which is more than a
    word list can say for a phrase or an idiom.
    """

    lemma: str
    part_of_speech: str
    context: str
    definition: str
    native_meaning: str
    provider: str = ""
    model: str = ""


@dataclass(frozen=True, slots=True)
class PassageTranslationResult:
    translation: str
    provider: str = ""
    model: str = ""


class ContextualMeaningProvider(Protocol):
    async def meaning_in_context(
        self,
        word: str,
        sentence: str,
        learner: LearnerContext,
    ) -> ContextualMeaning: ...


class PassageTranslator(Protocol):
    async def translate_passage(
        self,
        text: str,
        target_language: str,
        preceding: str = "",
        book_title: str = "",
    ) -> PassageTranslationResult: ...


class ContextualMeaningMemo(Protocol):
    """Where a paid-for contextual meaning is remembered. Best-effort by
    contract: implementations swallow their own failures and answer ``None``."""

    def meaning_key(self, word: str, sentence: str, native_language: str, version: int) -> str: ...

    async def get_meaning(self, key: str) -> ContextualMeaning | None: ...

    async def put_meaning(self, key: str, meaning: ContextualMeaning) -> None: ...
