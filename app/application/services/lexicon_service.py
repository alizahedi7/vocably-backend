"""Reading from and writing to the shared lexicon.

The one place that decides what becomes durable shared knowledge. Two callers,
deliberately:

* :class:`~app.infrastructure.ai.lexicon_ai_service.LexiconAIService` — the
  request path, writing through on every lookup a learner pays for.
* :class:`~app.application.services.deck_build_service.DeckBuildService` — the
  build pipeline, reading first and generating only what is missing.

Because both go through here, "a word a learner looked up last month costs the
deck builder nothing" is true by construction rather than by discipline.

Every write is validated first (:class:`SenseValidator`) and every write is
**append-only**: senses are added, never rewritten, and a ``sense_key`` that
already exists is left exactly as it is. The row already there may carry a
human's approval, and the writer here is always a machine.
"""

from __future__ import annotations

from app.application.ports.ai_service import (
    LearnerContext,
    LookupResult,
    LookupStatus,
    MeaningSuggestion,
)
from app.application.ports.lookup_cache import normalize_lookup_input
from app.core.logging import get_logger
from app.domain.entities.deck_build import SenseHint
from app.domain.entities.lexeme import (
    MAX_SENSES_PER_LEXEME,
    Lexeme,
    LexemeSense,
    SenseTranslation,
    sense_key_for,
)
from app.domain.enums import SenseSource, SenseStatus
from app.domain.repositories.lexicon_repository import LexiconRepository
from app.domain.services.sense_validation import SenseValidator

logger = get_logger("vocably.lexicon")


class LexiconService:
    def __init__(
        self,
        lexicon: LexiconRepository,
        *,
        content_version: int,
        provider: str = "",
        model: str = "",
        max_senses: int = MAX_SENSES_PER_LEXEME,
    ) -> None:
        self._lexicon = lexicon
        self._content_version = content_version
        self._provider = provider
        self._model = model
        self._max_senses = max_senses

    # ── Reads ─────────────────────────────────────────────────

    async def find(self, term: str, *, language: str = "en") -> Lexeme | None:
        """The lexeme for raw input, normalized the same way the cache does."""
        lemma = normalize_lookup_input(term)
        if not lemma:
            return None
        return await self._lexicon.get(lemma, language=language)

    async def as_lookup_result(
        self,
        lexeme: Lexeme,
        *,
        native_language: str,
        register: str = "adult",
    ) -> LookupResult | None:
        """Render stored knowledge as the DTO the lookup path already speaks.

        ``None`` when nothing in this lexeme can be shown in the requested
        language — which is a *miss*, not an empty answer. The caller falls
        through to the provider, and the resulting translations top up the same
        senses rather than duplicating them.
        """
        suggestions = [
            suggestion
            for sense in lexeme.servable_senses(register)
            if (suggestion := sense.to_suggestion(native_language)) is not None
        ]
        if not suggestions:
            return None
        return LookupResult(
            term=lexeme.display_term or lexeme.lemma,
            suggestions=suggestions[: self._max_senses],
            status=LookupStatus.OK,
            notice=None,
            phonetic=lexeme.phonetic or "",
        )

    # ── Writes ────────────────────────────────────────────────

    async def record(
        self,
        result: LookupResult,
        learner: LearnerContext,
        *,
        source: SenseSource,
        register: str = "adult",
        language: str = "en",
    ) -> Lexeme | None:
        """Persist a provider answer as shared knowledge.

        Returns the lexeme as stored, or ``None`` when nothing was worth keeping.
        ``UNSUPPORTED`` writes nothing at all: the lookup cache already remembers
        "this input is not a word", with a TTL, which is the right place for it —
        the lexicon is a record of words, and unintelligible input is not one.
        """
        if result.status is LookupStatus.UNSUPPORTED or not result.suggestions:
            return None

        lemma = normalize_lookup_input(result.term)
        if not lemma:
            return None

        validator = SenseValidator(
            native_language=learner.native_language,
            max_senses=self._max_senses,
        )
        outcome = validator.validate(result.suggestions)
        if outcome.rejected:
            logger.info("lexicon rejected %d sense(s) for %r", len(outcome.rejected), lemma)
        if outcome.is_empty:
            # Nothing survived. Deliberately not an error here — the learner is
            # still served the provider's answer; it just does not become
            # everybody's.
            logger.warning("lexicon stored nothing for %r: %s", lemma, outcome.summary())
            return None

        lexeme = await self._lexicon.upsert(
            lemma,
            language=language,
            display_term=result.term.strip() or lemma,
            # Only ever fills a NULL. '' is a real answer ("no IPA exists") and
            # must survive a later lookup that also found none.
            phonetic=result.phonetic if result.phonetic else None,
        )

        status = SenseStatus.NEEDS_REVIEW if outcome.needs_review else SenseStatus.AUTO
        # Two halves, and both matter. New senses are appended; senses we already
        # hold get a headline in this learner's language if they lack one. The
        # second half is what the translations table exists for — a learner whose
        # native language is new to the platform re-buys short headlines, not the
        # English corpus.
        await self._top_up_translations(lexeme, outcome.accepted, learner.native_language, register)
        senses = self._build_senses(
            lexeme,
            outcome.accepted,
            native_language=learner.native_language,
            register=register,
            status=status,
            source=source,
        )
        if senses:
            await self._lexicon.add_senses(lexeme.id, senses)
        return await self._lexicon.get_by_id(lexeme.id)

    async def _top_up_translations(
        self,
        lexeme: Lexeme,
        suggestions: list[MeaningSuggestion],
        native_language: str,
        register: str,
    ) -> None:
        by_key = {s.sense_key: s for s in lexeme.senses if s.register == register}
        for suggestion in suggestions:
            existing = by_key.get(sense_key_for(suggestion.part_of_speech, suggestion.context))
            if existing is None or existing.translation_for(native_language) is not None:
                continue
            await self._lexicon.add_translation_if_absent(
                existing.id,
                native_language=native_language,
                native_meaning=suggestion.native_meaning.strip(),
                content_version=self._content_version,
            )

    async def append_senses(
        self,
        lexeme: Lexeme,
        suggestions: list[MeaningSuggestion],
        learner: LearnerContext,
        *,
        source: SenseSource = SenseSource.ENRICHMENT,
        register: str = "adult",
    ) -> Lexeme:
        """Add senses to a lexeme that already exists, without touching its own.

        The enrichment write path. Positions continue from what is stored, so a
        deck already pointing at position 1 keeps pointing at the same sense, and
        the cap is applied against what is *already there* rather than against
        this batch.
        """
        validator = SenseValidator(
            native_language=learner.native_language,
            max_senses=self._max_senses,
        )
        outcome = validator.validate(suggestions)
        if outcome.is_empty:
            return lexeme

        status = SenseStatus.NEEDS_REVIEW if outcome.needs_review else SenseStatus.AUTO
        senses = self._build_senses(
            lexeme,
            outcome.accepted,
            native_language=learner.native_language,
            register=register,
            status=status,
            source=source,
        )
        if not senses:
            return lexeme
        await self._lexicon.add_senses(lexeme.id, senses)
        refreshed = await self._lexicon.get_by_id(lexeme.id)
        return refreshed or lexeme

    def missing_sense_keys(self, lexeme: Lexeme, hint: SenseHint, register: str = "adult") -> bool:
        """Whether the sense a deck pinned is genuinely absent.

        Only meaningful for a pinned hint — a free-text gloss describes a sense
        rather than naming one, and "absent" is then a judgement the selector
        makes by score, not a lookup.
        """
        if not hint.is_pinned:
            return False
        wanted = sense_key_for(hint.part_of_speech, hint.context)
        return lexeme.sense_by_key(wanted, register) is None

    # ── Helpers ───────────────────────────────────────────────

    def _build_senses(
        self,
        lexeme: Lexeme,
        suggestions: list[MeaningSuggestion],
        *,
        native_language: str,
        register: str,
        status: SenseStatus,
        source: SenseSource,
    ) -> list[LexemeSense]:
        position = lexeme.next_position(register)
        existing_keys = {s.sense_key for s in lexeme.senses if s.register == register}
        room = MAX_SENSES_PER_LEXEME - len(existing_keys)
        built: list[LexemeSense] = []

        for suggestion in suggestions:
            key = sense_key_for(suggestion.part_of_speech, suggestion.context)
            if key in existing_keys:
                # Already known. Not an error and not worth a write: the stored
                # row wins, because it may have been reviewed.
                continue
            if room <= 0:
                break
            sense = LexemeSense(
                lexeme_id=lexeme.id,
                sense_key=key,
                register=register,
                position=position,
                part_of_speech=suggestion.part_of_speech.strip(),
                context=suggestion.context.strip(),
                definition=suggestion.definition.strip(),
                example=suggestion.example.strip(),
                status=status,
                content_version=self._content_version,
                provider=self._provider,
                model=self._model,
                source=source,
            )
            sense.translations = [
                SenseTranslation(
                    sense_id=sense.id,
                    native_language=native_language,
                    native_meaning=suggestion.native_meaning.strip(),
                    status=status,
                    content_version=self._content_version,
                )
            ]
            built.append(sense)
            existing_keys.add(key)
            position += 1
            room -= 1
        return built
