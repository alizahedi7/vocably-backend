"""Ports: the two AI capabilities the reader needs beyond ``AIService``.

Structural protocols, exactly like ``SenseTranslator`` and ``SenseEnricher``:
every provider adapter grows these two methods, ``FailoverAIService`` delegates
them, and the reader service is handed ``raw_ai_provider()`` cast to each.
Miss one in the failover delegation and the reader loses failover at runtime
with no type error to say so — the rule ``CLAUDE.md`` already states for the
other five.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.application.ports.ai_service import LearnerContext, MeaningSuggestion


@dataclass(frozen=True, slots=True)
class Disambiguation:
    """Which of the offered senses a sentence uses.

    ``index`` is a position in the list the model was shown, or ``-1`` when
    none fits. The caller treats ``-1`` as "show every sense", never as an
    error — and as the signal that the lexicon is missing a sense.
    """

    index: int
    confidence: float
    provider: str = ""
    model: str = ""


@dataclass(frozen=True, slots=True)
class PassageTranslationResult:
    translation: str
    provider: str = ""
    model: str = ""


class SenseDisambiguator(Protocol):
    async def disambiguate_sense(
        self,
        term: str,
        sentence: str,
        senses: list[MeaningSuggestion],
        learner: LearnerContext,
    ) -> Disambiguation: ...


class PassageTranslator(Protocol):
    async def translate_passage(
        self,
        text: str,
        target_language: str,
        preceding: str = "",
        book_title: str = "",
    ) -> PassageTranslationResult: ...


class DisambiguationMemo(Protocol):
    """Where a paid-for disambiguation is remembered. Best-effort by contract:
    implementations swallow their own failures and answer ``None``."""

    def disambiguation_key(self, lookup_id: str, sentence: str, prompt_version: int) -> str: ...

    async def get_disambiguation(self, key: str) -> int | None: ...

    async def put_disambiguation(self, key: str, index: int) -> None: ...
