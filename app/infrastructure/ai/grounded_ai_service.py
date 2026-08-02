"""Dictionary-grounded lookups, with full generation as the fallback.

A decorator over :class:`AIService`, in the same spirit as
:class:`CachingAIService`: the application layer keeps calling one port and never
learns that a dictionary exists. That matters more here than for the cache,
because which path answered is genuinely invisible — both end at the same
:class:`LookupResult` and the same :class:`MeaningSuggestion` list.

Order of composition is deliberate::

    AIStudioService
      └─ CachingAIService          # cheapest possible answer, no network
          └─ GroundedAIService     # dictionary + targeted translation
              └─ AvalAIService     # full generation, the universal fallback

The cache sits **outside**, so a cached card costs nothing regardless of which
path minted it. Grounding sits **inside**, so its decisions are cached too and a
popular word is grounded once for everybody.

**The dictionary is never load-bearing.** A miss, a timeout, a 429, a malformed
payload, a translation that fails validation, or a model that returns nothing
usable all end in the same place: the inner service generates the card exactly as
it does today. There is no input for which this decorator produces a worse
outcome than not having it — only a slower one, bounded by the dictionary
timeout.

Two grounded modes, selected by ``rewrite_definitions``:

*translate-only* (default)
    The model returns Persian headlines keyed by sense index; the dictionary's
    own definition, example and part of speech are joined back locally. Cheapest,
    and the model cannot corrupt English it never writes.
*rewrite*
    The model also rewrites each definition into learner-dictionary English.
    Costs more output tokens, and buys readable card fronts when the source
    wording is dense — which, for a Wiktionary mirror, it often is.
"""

from __future__ import annotations

from typing import Protocol

from app.application.ports.ai_service import (
    AIService,
    GeneratedStory,
    LearnerContext,
    LookupResult,
    LookupStatus,
    MeaningSuggestion,
)
from app.application.ports.dictionary_service import (
    DictionaryEntry,
    DictionarySense,
    DictionaryService,
)
from app.core.logging import get_logger
from app.infrastructure.ai.translate_prompts import render_entry

logger = get_logger("vocably.ai.grounded")

#: Senses handed to the model to choose from. Well above the 3-4 cards we want,
#: far below the 63 the API returns for "run": enough that the right sense is
#: present, few enough that it is not buried.
_CANDIDATE_SENSES = 12

#: Longest input worth a dictionary call. Past this it is a sentence, not a
#: headword, and the generative path extracts the term from those.
_MAX_GROUNDABLE_CHARS = 40

#: Above this codepoint the text is not Latin script, so an English dictionary
#: cannot serve it. Latin-1 accents stay in: "café" and "naïve" are ordinary
#: English entries.
_LATIN_MAX_CODEPOINT = 0x0250


class SenseTranslator(Protocol):
    """Localises dictionary senses. Implemented by the provider adapters.

    A structural protocol, not a base class, so an adapter satisfies it just by
    having the methods — no second inheritance chain alongside
    :class:`AIService`, and no import from the adapters back into this module.
    In practice the same object is passed as both ``inner`` and ``translator``.
    """

    async def translate_only(
        self,
        term: str,
        entry_text: str,
        learner: LearnerContext,
        max_cards: int,
    ) -> list[tuple[int, str, str]]:
        """Return ``(sense index, native_meaning, context)`` per selected sense."""
        ...

    async def translate_senses(
        self,
        term: str,
        entry_text: str,
        learner: LearnerContext,
        max_cards: int,
    ) -> list[MeaningSuggestion]: ...


class GroundedAIService(AIService):
    def __init__(
        self,
        inner: AIService,
        dictionary: DictionaryService,
        translator: SenseTranslator,
        *,
        max_cards: int = 4,
        rewrite_definitions: bool = False,
    ) -> None:
        self._inner = inner
        self._dictionary = dictionary
        self._translator = translator
        self._max_cards = max_cards
        self._rewrite = rewrite_definitions

    async def look_up_meanings(self, term: str, learner: LearnerContext) -> LookupResult:
        if not self._is_groundable(term):
            return await self._inner.look_up_meanings(term, learner)

        entry = await self._safe_look_up(term)
        if entry is None:
            # Covers ~33% of real input: typos (0% dictionary coverage), most
            # idioms (25%), newer slang (50%), and every non-English term.
            return await self._inner.look_up_meanings(term, learner)

        candidates = entry.top(_CANDIDATE_SENSES)
        try:
            suggestions = await self._build(term, entry, candidates, learner)
        except Exception as exc:  # noqa: BLE001 — any failure falls back, never 502s
            logger.warning("grounded lookup failed, generating instead: %s", type(exc).__name__)
            return await self._inner.look_up_meanings(term, learner)

        suggestions = [s for s in suggestions if s.definition and s.native_meaning]
        if not suggestions:
            # A grounded call that yields nothing usable is worse than no
            # grounding: fall through rather than hand the learner an empty deck.
            logger.warning("grounded lookup returned no usable senses for %r", entry.term)
            return await self._inner.look_up_meanings(term, learner)

        return LookupResult(
            term=entry.term,
            suggestions=suggestions[: self._max_cards],
            status=LookupStatus.OK,
            notice=None,
            # Free, authoritative, and the one field a model must never invent.
            phonetic=entry.phonetic,
        )

    async def generate_story(self, words: list[str], learner: LearnerContext) -> GeneratedStory:
        # Nothing to ground: a story is generated prose, not a dictionary fact.
        return await self._inner.generate_story(words, learner)

    # ── Grounded assembly ─────────────────────────────────────

    async def _build(
        self,
        term: str,
        entry: DictionaryEntry,
        candidates: list[DictionarySense],
        learner: LearnerContext,
    ) -> list[MeaningSuggestion]:
        if self._rewrite:
            return await self._translator.translate_senses(
                term=entry.term,
                entry_text=render_entry(candidates),
                learner=learner,
                max_cards=self._max_cards,
            )

        translations = await self._translator.translate_only(
            term=entry.term,
            entry_text=render_entry(candidates, numbered=True),
            learner=learner,
            max_cards=self._max_cards,
        )
        return self._join(candidates, translations)

    @staticmethod
    def _join(
        candidates: list[DictionarySense],
        translations: list[tuple[int, str, str]],
    ) -> list[MeaningSuggestion]:
        """Rejoin model translations to the dictionary senses they describe.

        An out-of-range index is dropped rather than clamped: pairing a Persian
        headline with the wrong English definition is the worst failure this card
        can have, and a card that never renders is recoverable where a
        confidently mismatched one is not. The same reasoning drops duplicates —
        two translations claiming one sense means the model lost track of the
        list, and only the first can be trusted.
        """
        joined: list[MeaningSuggestion] = []
        seen: set[int] = set()
        for index, native_meaning, context in translations:
            if index < 0 or index >= len(candidates) or index in seen:
                logger.warning("dropping translation with unusable index %s", index)
                continue
            seen.add(index)
            sense = candidates[index]
            joined.append(
                MeaningSuggestion(
                    native_meaning=native_meaning,
                    # Straight from the dictionary — never model-authored.
                    definition=sense.definition,
                    example=sense.example,
                    part_of_speech=sense.part_of_speech,
                    context=context,
                )
            )
        return joined

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _is_groundable(term: str) -> bool:
        """Cheap pre-filter for input an English dictionary cannot serve.

        Only the obvious cases, on purpose. It is tempting to also skip
        multi-word input, but measurement says otherwise: the API covers 70% of
        phrasal verbs ("put off", "get across", "take on" all hit). At ~27 ms a
        speculative call is cheaper than the reasoning needed to predict a miss,
        so anything that might be an English headword is tried.
        """
        stripped = term.strip()
        if not stripped or len(stripped) > _MAX_GROUNDABLE_CHARS:
            return False
        return all(ord(ch) < _LATIN_MAX_CODEPOINT for ch in stripped)

    async def _safe_look_up(self, term: str) -> DictionaryEntry | None:
        try:
            return await self._dictionary.look_up(term)
        except Exception as exc:  # noqa: BLE001 — the port promises not to raise; belt and braces
            logger.info("dictionary raised, generating instead: %s", type(exc).__name__)
            return None
