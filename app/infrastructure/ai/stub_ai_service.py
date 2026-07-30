"""Deterministic stub adapter for :class:`AIService`.

Returns canned-but-plausible data with no external calls, so the whole app runs offline.
Swap this for a Claude/OpenAI adapter (same interface) when wiring a real provider — see
``app.api.deps.get_ai_provider``.
"""

from __future__ import annotations

import asyncio

from app.application.ports.ai_service import (
    AIService,
    GeneratedStory,
    LearnerContext,
    LookupResult,
    LookupStatus,
    MeaningSuggestion,
)

# Human-readable theme phrases per interest topic id, used to flavor stub output the way a
# real model would weave the learner's interests into examples and stories.
_TOPIC_PHRASES: dict[str, str] = {
    "daily": "everyday life",
    "travel": "a trip abroad",
    "work": "a day at the office",
    "movies": "a night at the movies",
    "music": "a favorite song",
    "food": "a delicious meal",
    "tech": "a new gadget",
    "sports": "a big match",
    "health": "a healthy routine",
    "science": "a curious experiment",
    "gaming": "an epic game",
    "news": "today's headlines",
}


def _theme(learner: LearnerContext) -> str | None:
    for topic in learner.interests:
        phrase = _TOPIC_PHRASES.get(topic)
        if phrase:
            return phrase
    return None


# Curated senses mirroring the design's AI_SENSES fixture: a native-language headline, an
# English learner-dictionary definition, and one reusable sentence per sense.
_CANNED_SENSES: dict[str, list[MeaningSuggestion]] = {
    "run": [
        MeaningSuggestion(
            native_meaning="to run, to jog",
            context="Movement",
            part_of_speech="verb",
            definition="to move using your legs, going faster than when you walk",
            example="I run in the park every morning.",
        ),
        MeaningSuggestion(
            native_meaning="to manage, to operate",
            context="Business",
            part_of_speech="verb",
            definition="to be in charge of a business, organization, or activity",
            example="She runs a small bakery downtown.",
        ),
        MeaningSuggestion(
            native_meaning="to work, to function",
            context="Machines",
            part_of_speech="verb",
            definition="(of a machine or engine) to operate or work in the correct way",
            example="The engine runs smoothly now.",
        ),
    ],
    "light": [
        MeaningSuggestion(
            native_meaning="light, brightness",
            context="Nature",
            part_of_speech="noun",
            definition=(
                "the brightness from the sun, a lamp, or a fire that makes it possible to "
                "see things"
            ),
            example="The light in this room is beautiful.",
        ),
        MeaningSuggestion(
            native_meaning="not heavy",
            context="Weight",
            part_of_speech="adjective",
            definition="weighing little; easy to lift or carry",
            example="This suitcase is surprisingly light.",
        ),
        MeaningSuggestion(
            native_meaning="pale in colour",
            context="Colours",
            part_of_speech="adjective",
            definition="pale, and closer to white than to black",
            example="She wore a light blue dress.",
        ),
    ],
    "book": [
        MeaningSuggestion(
            native_meaning="a book",
            context="Reading",
            part_of_speech="noun",
            definition="a set of printed pages held together in a cover, that you read",
            example="This book is very interesting.",
        ),
        MeaningSuggestion(
            native_meaning="to reserve, to book",
            context="Travel",
            part_of_speech="verb",
            definition="to arrange to have a seat, room, or ticket kept for you in advance",
            example="I booked a hotel room online.",
        ),
    ],
}


class StubAIService(AIService):
    """A fake AI provider useful for local dev and tests."""

    def __init__(self, latency_seconds: float = 0.0) -> None:
        # Optional simulated latency to exercise loading states.
        self._latency = latency_seconds

    async def look_up_meanings(
        self,
        term: str,
        learner: LearnerContext,
    ) -> LookupResult:
        await self._delay()
        key = term.strip().lower()
        if key in _CANNED_SENSES:
            return LookupResult(term=key, suggestions=_CANNED_SENSES[key])
        theme = _theme(learner)
        example = (
            f"While thinking about {theme}, they used “{term}” naturally in a sentence."
            if theme
            else f"Here is “{term}” used naturally in a sentence."
        )
        # Generic fallback so any word yields a usable suggestion.
        return LookupResult(
            term=term.strip(),
            suggestions=[
                MeaningSuggestion(
                    native_meaning=f"“{term}” in {learner.native_language}",
                    context=(theme or "general").title(),
                    part_of_speech="noun",
                    definition=f"the most common everyday sense of “{term}”",
                    example=example,
                )
            ],
            status=LookupStatus.OK,
        )

    async def generate_story(
        self,
        words: list[str],
        learner: LearnerContext,
    ) -> GeneratedStory:
        await self._delay()
        joined = ", ".join(words[:-1]) + (f" and {words[-1]}" if len(words) > 1 else "")
        theme = _theme(learner)
        opening = f"One quiet afternoon, dreaming of {theme}," if theme else "One quiet afternoon,"
        text = (
            f"{opening} a curious learner set out to use every word they had "
            f"mastered — {joined}. By the end of the day, each one felt like an old friend, "
            f"and the story they made together was one worth remembering."
        )
        return GeneratedStory(text=text, words_used=list(words))

    async def _delay(self) -> None:
        if self._latency > 0:
            await asyncio.sleep(self._latency)
