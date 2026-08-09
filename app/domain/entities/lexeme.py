"""The lexicon: what the platform knows about a word, independent of any deck.

The distinction this module exists to hold, and the one easiest to lose:

``ai_lookup_entries``
    A **cache of requests**. Keyed by ``(prompt_version, native_language,
    age_bucket, normalized input)``, disposable by design, swept wholesale when a
    prompt changes, and explicitly never load-bearing.

:class:`Lexeme` / :class:`LexemeSense`
    A **durable record of a word**. Keyed by the lemma, with a stable id per
    sense that a deck card can point at, a review status a human can set, and a
    ``content_version`` that a prompt bump marks stale rather than deleting.

Both are impersonal — no ``user_id`` reaches either — and a learner's edits to
their own card never flow back into either. What is new here is *permanence*:
a deck references senses, so senses must outlive the prompt that wrote them.

The English half of a sense (definition, example, part of speech, context) is
language-neutral and lives on :class:`LexemeSense`. Only the learner-facing
headline is per-language, on :class:`SenseTranslation`. That split is what makes
a second native language cost a translation pass rather than a second corpus.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.ports.ai_service import MeaningSuggestion
from app.domain.enums import SenseSource, SenseStatus

#: The card deck renders at most four backs, so the lexicon stores at most four
#: senses per lexeme. A product cap, not a data invariant — enforced here rather
#: than by a database constraint, because a constraint would fail an enrichment
#: write in a way nothing downstream could recover from.
MAX_SENSES_PER_LEXEME = 4

_SLUG_NOISE = re.compile(r"[^a-z0-9]+")


def sense_key_for(part_of_speech: str, context: str) -> str:
    """The stable identity of a sense within its lexeme.

    Derived from what the sense *is* rather than assigned randomly, because
    enrichment has to answer "do we already have this one?" without an AI call
    and without a fuzzy match. Two senses agreeing on both part of speech and
    context label are the same card for every purpose this app has.

    Slugged so that "Phrasal verb"/"phrasal-verb" and "Business"/"business"
    cannot both exist as separate senses of one word.
    """
    pos = _SLUG_NOISE.sub("-", part_of_speech.strip().casefold()).strip("-")
    ctx = _SLUG_NOISE.sub("-", context.strip().casefold()).strip("-")
    return f"{pos or 'unknown'}:{ctx or 'general'}"


@dataclass(slots=True)
class SenseTranslation:
    """One sense's headline in one learner language."""

    id: UUID = field(default_factory=uuid4)
    sense_id: UUID = field(default_factory=uuid4)
    #: Stored as the app spells it ("Persian"), matching ``users.native_language``
    #: and ``LearnerContext``. Not a BCP-47 tag: nothing here maps to one, and
    #: inventing a second spelling of the same fact invites the two to disagree.
    native_language: str = ""
    native_meaning: str = ""
    status: SenseStatus = SenseStatus.AUTO
    content_version: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True)
class LexemeSense:
    """One meaning of one word, in English, with its translations attached."""

    id: UUID = field(default_factory=uuid4)
    lexeme_id: UUID = field(default_factory=uuid4)
    #: See :func:`sense_key_for`. Unique per ``(lexeme, register)``.
    sense_key: str = ""
    #: Which audience this wording was written for — the same three buckets the
    #: lookup cache uses, because they are the only ones that change the text.
    register: str = "adult"
    #: Ordering as the provider or dictionary ranked it, most common first.
    #: **Never renumbered**: a published deck may point at position 2.
    position: int = 0
    part_of_speech: str = ""
    #: The chip above the card back — "Movement", "Management". English, short.
    context: str = ""
    definition: str = ""
    example: str = ""
    status: SenseStatus = SenseStatus.AUTO
    #: ``deps._effective_prompt_version()`` at the moment this row was written.
    #: Identifies the whole pipeline (prompt + grounding mode), not one prompt.
    content_version: int = 0
    provider: str = ""
    model: str = ""
    source: SenseSource = SenseSource.LOOKUP
    translations: list[SenseTranslation] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def translation_for(self, native_language: str) -> SenseTranslation | None:
        wanted = native_language.strip().casefold()
        for translation in self.translations:
            if translation.native_language.strip().casefold() == wanted:
                return translation
        return None

    @property
    def is_servable(self) -> bool:
        """Whether this sense may be shown to anyone at all."""
        return self.status is not SenseStatus.REJECTED

    def to_suggestion(self, native_language: str) -> MeaningSuggestion | None:
        """Render as the DTO the lookup path and the card already speak.

        ``None`` when this sense has no headline in the requested language: a
        card with an empty front is worse than one fewer card, and the caller
        treats a short list as a partial hit worth topping up.
        """
        translation = self.translation_for(native_language)
        if translation is None or not translation.native_meaning.strip():
            return None
        return MeaningSuggestion(
            native_meaning=translation.native_meaning,
            definition=self.definition,
            example=self.example,
            context=self.context,
            part_of_speech=self.part_of_speech,
        )


@dataclass(slots=True)
class Lexeme:
    """A headword and everything known about it."""

    id: UUID = field(default_factory=uuid4)
    #: ``normalize_lookup_input`` applied — the deduplication key, and the only
    #: thing a lookup is matched against.
    lemma: str = ""
    language: str = "en"
    #: The spelling a learner sees, in the provider's or dictionary's casing.
    display_term: str = ""
    #: IPA. ``None`` means *no answer yet*; ``""`` means *the dictionary
    #: answered and this word has no transcription* — roughly a third of them.
    #: The distinction is what stops the backfill re-asking permanent misses
    #: nightly forever, and it is carried here unchanged from ``words.phonetic``.
    phonetic: str | None = None
    senses: list[LexemeSense] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def servable_senses(self, register: str = "adult") -> list[LexemeSense]:
        """Senses fit to show, in stored order, for one audience."""
        return [s for s in self.senses if s.register == register and s.is_servable]

    def sense_by_key(self, sense_key: str, register: str = "adult") -> LexemeSense | None:
        for sense in self.senses:
            if sense.sense_key == sense_key and sense.register == register:
                return sense
        return None

    def next_position(self, register: str = "adult") -> int:
        """Where an appended sense goes. Existing positions are never reused."""
        taken = [s.position for s in self.senses if s.register == register]
        return max(taken) + 1 if taken else 0

    def has_room(self, register: str = "adult") -> bool:
        return len([s for s in self.senses if s.register == register]) < MAX_SENSES_PER_LEXEME
