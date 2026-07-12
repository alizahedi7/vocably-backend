"""Deterministic stub adapter for :class:`AIService`.

Returns canned-but-plausible data with no external calls, so the whole app runs offline.
Swap this for a Claude/OpenAI adapter (same interface) when wiring a real provider — see
``app.api.deps.get_ai_service``.
"""

from __future__ import annotations

import asyncio

from app.application.ports.ai_service import (
    AIService,
    GeneratedStory,
    MeaningSuggestion,
)

# Curated senses mirroring the design's AI_SENSES fixture.
_CANNED_SENSES: dict[str, list[MeaningSuggestion]] = {
    "run": [
        MeaningSuggestion("to move fast on foot", "movement", "I run in the park every morning."),
        MeaningSuggestion("to manage or operate", "business", "She runs a small bakery downtown."),
        MeaningSuggestion("to work or function", "machines", "The engine runs smoothly now."),
    ],
    "light": [
        MeaningSuggestion(
            "brightness that lets you see", "nature", "The light in this room is beautiful."
        ),
        MeaningSuggestion("not heavy", "weight", "This suitcase is surprisingly light."),
        MeaningSuggestion("pale in color", "colors", "She wore a light blue dress."),
    ],
    "book": [
        MeaningSuggestion("printed pages you read", "reading", "This book is very interesting."),
        MeaningSuggestion("to reserve in advance", "travel", "I booked a hotel room online."),
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
        native_language: str,
    ) -> list[MeaningSuggestion]:
        await self._delay()
        key = term.strip().lower()
        if key in _CANNED_SENSES:
            return _CANNED_SENSES[key]
        # Generic fallback so any word yields a usable suggestion.
        return [
            MeaningSuggestion(
                meaning=f"a common sense of “{term}” (explained in {native_language})",
                context="general",
                example=f"Here is “{term}” used naturally in a sentence.",
            )
        ]

    async def generate_story(
        self,
        words: list[str],
        native_language: str,
    ) -> GeneratedStory:
        await self._delay()
        joined = ", ".join(words[:-1]) + (f" and {words[-1]}" if len(words) > 1 else "")
        text = (
            f"One quiet afternoon, a curious learner set out to use every word they had "
            f"mastered — {joined}. By the end of the day, each one felt like an old friend, "
            f"and the story they made together was one worth remembering."
        )
        return GeneratedStory(text=text, words_used=list(words))

    async def _delay(self) -> None:
        if self._latency > 0:
            await asyncio.sleep(self._latency)
