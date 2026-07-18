"""Port: AI capabilities used by the AI Studio use cases.

The application depends only on this interface. A stub adapter ships today; a real
Claude/OpenAI adapter can be dropped in later without touching business logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class LearnerContext:
    """Profile facts the AI uses to personalize its output.

    ``interests`` themes examples/stories to the learner's topics; ``age_range``
    keeps content age-appropriate.
    """

    native_language: str = "English"
    age_range: str | None = None
    interests: Sequence[str] = field(default_factory=tuple)


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
        learner: LearnerContext,
    ) -> list[MeaningSuggestion]:
        """Return candidate senses for ``term``, explained in the learner's language.

        Examples should be themed to ``learner.interests`` and appropriate for
        ``learner.age_range`` when set.
        """

    @abstractmethod
    async def generate_story(
        self,
        words: list[str],
        learner: LearnerContext,
    ) -> GeneratedStory:
        """Write a short story that naturally uses the supplied ``words``.

        The story should be themed to ``learner.interests`` and appropriate for
        ``learner.age_range`` when set.
        """
