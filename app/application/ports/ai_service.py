"""Port: AI capabilities used by the AI Studio use cases.

The application depends only on this interface. A stub adapter ships today; a real
Claude/OpenAI adapter can be dropped in later without touching business logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class LearnerContext:
    """Profile facts the AI uses to personalize its output.

    ``interests`` themes examples/stories to the learner's topics; ``age_range``
    keeps content age-appropriate.
    """

    native_language: str = "English"
    age_range: str | None = None
    interests: Sequence[str] = field(default_factory=tuple)


class LookupStatus(StrEnum):
    """How the provider interpreted the raw text the learner typed.

    The client uses this to decide whether to show a notice above the suggestion
    deck ("Showing results for …") and whether to trust the senses at all.
    """

    #: The input was a clean target-language word/phrase; senses are for it as typed.
    OK = "ok"
    #: A typo was corrected — ``LookupResult.term`` holds the corrected spelling.
    CORRECTED = "corrected"
    #: A full sentence was submitted; the key word/idiom was extracted.
    EXTRACTED = "extracted"
    #: The input was in the learner's native language; ``term`` is the target-language word.
    TRANSLATED = "translated"
    #: Input was unintelligible, or not a lexical item. ``suggestions`` is empty.
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class MeaningSuggestion:
    """One sense of a word, as suggested by the AI when adding a word.

    Mirrors one card back in the "AI Card Magic" deck. The first three fields are
    the original, narrower contract and stay positional so existing callers keep
    working; everything after them is optional enrichment a provider may omit.
    """

    #: Short target-language gloss ("to move fast on foot") — the card's subtitle.
    meaning: str
    #: Sense label that disambiguates this card from its siblings ("Movement").
    context: str
    #: The single headline example. Mirrors ``examples[0]`` when examples are present.
    example: str
    #: "noun" | "verb" | "adjective" | … — shown italic next to the context chip.
    part_of_speech: str = ""
    #: The meaning rendered in the learner's native language — the card's headline.
    native_meaning: str = ""
    #: Learner-dictionary style definition in the target language (Longman/Oxford register).
    definition: str = ""
    #: Practical sentences for *this* sense. Includes ``example`` as its first entry.
    examples: tuple[str, ...] = ()
    synonyms: tuple[str, ...] = ()
    antonyms: tuple[str, ...] = ()
    collocations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LookupResult:
    """The full answer to one lookup, including how the input was interpreted."""

    #: The term the suggestions actually describe — may differ from what was typed
    #: when the input was corrected, extracted from a sentence, or translated.
    term: str
    suggestions: list[MeaningSuggestion]
    status: LookupStatus = LookupStatus.OK
    #: Short user-facing explanation, set whenever ``status`` is not ``OK``.
    notice: str | None = None


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
    ) -> LookupResult:
        """Return candidate senses for ``term``, explained in the learner's language.

        ``term`` is raw learner input and may be a sentence, a native-language word,
        or a typo; implementations resolve it and report how via
        :class:`LookupStatus` rather than raising. Examples should be themed to
        ``learner.interests`` and appropriate for ``learner.age_range`` when set.

        Implementations must return at most 4 suggestions — the card deck in the
        "AI Card Magic" screen shows no more than that.
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
