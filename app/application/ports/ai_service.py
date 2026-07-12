"""Port: AI capabilities used by the AI Studio use cases.

The application depends only on this interface. A stub adapter ships today; a real
Claude/OpenAI adapter can be dropped in later without touching business logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MeaningSuggestion:
    """One sense of a word, as suggested by the AI when adding a word."""

    meaning: str
    context: str
    example: str


@dataclass(frozen=True, slots=True)
class GeneratedStory:
    """A short practice story woven from the learner's mastered words."""

    text: str
    words_used: list[str]


class AIService(ABC):
    @abstractmethod
    async def look_up_meanings(
        self,
        term: str,
        native_language: str,
    ) -> list[MeaningSuggestion]:
        """Return candidate senses for ``term``, explained in ``native_language``."""

    @abstractmethod
    async def generate_story(
        self,
        words: list[str],
        native_language: str,
    ) -> GeneratedStory:
        """Write a short story that naturally uses the supplied ``words``."""
