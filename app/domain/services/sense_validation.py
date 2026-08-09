"""Whether a generated sense is fit to become shared content.

The provider adapters already refuse to trust the *shape* of a response
(``payloads.py``) and the cache refuses to trust what it stored
(``lookup_cache_payload.py``). This is the third refusal: the shape is right and
the content is still wrong.

Two severities, and the split is the whole design:

**Reject** — the sense cannot be a card. An empty definition, a part of speech
that is a sentence, a Persian headline written in English. These are cheap to
detect, impossible to render, and worth one retry.

**Warn** — the sense is probably fine and possibly not. An example that does not
visibly contain the headword, a headline that merely echoes the term. English
morphology and Persian orthography both make these unreliable as hard rules, and
a 504-word build must not stop because one example sentence used "ran". Warnings
set :class:`~app.domain.enums.SenseStatus.NEEDS_REVIEW`, which puts the sense in
front of a human without blocking anything.

Nothing here calls a model. A model judging a model's output is a second thing
that can be confidently wrong, at twice the price.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.application.ports.ai_service import MeaningSuggestion
from app.domain.entities.lexeme import sense_key_for

#: Grammatical names a card may show. Anything else is the model explaining
#: itself in a field the client renders in italics beside the sense chip.
VALID_PARTS_OF_SPEECH = frozenset(
    {
        "noun",
        "verb",
        "adjective",
        "adverb",
        "pronoun",
        "preposition",
        "conjunction",
        "interjection",
        "determiner",
        "phrase",
        "phrasal verb",
        "idiom",
        "abbreviation",
        "prefix",
        "suffix",
    }
)

MAX_DEFINITION_CHARS = 400
MAX_EXAMPLE_CHARS = 300
MAX_NATIVE_MEANING_CHARS = 200
MAX_CONTEXT_WORDS = 3
MAX_CONTEXT_CHARS = 40

#: Languages whose text must not be Latin script, and the Unicode block name
#: prefix that proves it. This catches the single most expensive failure that
#: survives schema validation: the model answering the "translate" instruction
#: in English, which looks perfectly well-formed in a log and is useless on a
#: card. Languages absent from this map are simply not script-checked.
_EXPECTED_SCRIPTS: dict[str, tuple[str, ...]] = {
    "persian": ("ARABIC",),
    "farsi": ("ARABIC",),
    "arabic": ("ARABIC",),
    "russian": ("CYRILLIC",),
    "ukrainian": ("CYRILLIC",),
    "greek": ("GREEK",),
    "hebrew": ("HEBREW",),
    "hindi": ("DEVANAGARI",),
    "japanese": ("HIRAGANA", "KATAKANA", "CJK"),
    "korean": ("HANGUL",),
    "chinese": ("CJK",),
    "thai": ("THAI",),
    "armenian": ("ARMENIAN",),
    "georgian": ("GEORGIAN",),
}

_WORD_RE = re.compile(r"[a-z]+")


@dataclass(frozen=True, slots=True)
class SenseIssue:
    code: str
    message: str


@dataclass(slots=True)
class ValidationOutcome:
    """What survived, and what a human should be told about it."""

    #: Senses fit to store, deduplicated, capped, in input order.
    accepted: list[MeaningSuggestion] = field(default_factory=list)
    #: Why each dropped sense was dropped. Ends up in ``last_error``.
    rejected: list[SenseIssue] = field(default_factory=list)
    #: Soft problems on senses that were nonetheless accepted.
    warnings: list[SenseIssue] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return bool(self.warnings)

    @property
    def is_empty(self) -> bool:
        """No usable sense survived — a generation failure, not an empty word."""
        return not self.accepted

    def summary(self) -> str:
        parts = [f"{i.code}: {i.message}" for i in (*self.rejected, *self.warnings)]
        return "; ".join(parts)


class SenseValidator:
    """Deterministic quality gate between a provider and the lexicon."""

    def __init__(self, *, native_language: str, max_senses: int) -> None:
        self._native_language = native_language
        self._max_senses = max_senses

    def validate(self, suggestions: list[MeaningSuggestion]) -> ValidationOutcome:
        outcome = ValidationOutcome()
        seen_keys: set[str] = set()
        seen_examples: set[str] = set()

        for index, sense in enumerate(suggestions):
            if len(outcome.accepted) >= self._max_senses:
                # Truncation, not rejection: the provider gave us more good
                # senses than a card deck renders, which is not an error.
                break

            hard = self._hard_failures(index, sense)
            if hard is not None:
                outcome.rejected.append(hard)
                continue

            key = sense_key_for(sense.part_of_speech, sense.context)
            if key in seen_keys:
                outcome.rejected.append(
                    SenseIssue(
                        "duplicate_sense",
                        f"sense {index} repeats {key!r}, which an earlier sense already claims",
                    )
                )
                continue
            example = sense.example.strip().casefold()
            if example and example in seen_examples:
                outcome.rejected.append(
                    SenseIssue("duplicate_example", f"sense {index} reuses an earlier example")
                )
                continue

            seen_keys.add(key)
            if example:
                seen_examples.add(example)
            outcome.warnings.extend(self._soft_failures(index, sense))
            outcome.accepted.append(sense)

        return outcome

    # ── Rules ─────────────────────────────────────────────────

    def _hard_failures(self, index: int, sense: MeaningSuggestion) -> SenseIssue | None:
        definition = sense.definition.strip()
        native = sense.native_meaning.strip()
        pos = sense.part_of_speech.strip().casefold()
        context = sense.context.strip()

        if not definition:
            return SenseIssue("empty_definition", f"sense {index} has no definition")
        if not native:
            return SenseIssue("empty_native_meaning", f"sense {index} has no native meaning")
        if len(definition) > MAX_DEFINITION_CHARS:
            return SenseIssue("definition_too_long", f"sense {index} definition exceeds bounds")
        if len(sense.example) > MAX_EXAMPLE_CHARS:
            return SenseIssue("example_too_long", f"sense {index} example exceeds bounds")
        if len(native) > MAX_NATIVE_MEANING_CHARS:
            return SenseIssue("native_meaning_too_long", f"sense {index} headline exceeds bounds")
        if pos not in VALID_PARTS_OF_SPEECH:
            return SenseIssue(
                "invalid_part_of_speech",
                f"sense {index} part of speech {sense.part_of_speech!r} is not a grammatical name",
            )
        if not context:
            return SenseIssue("empty_context", f"sense {index} has no context label")
        if len(context) > MAX_CONTEXT_CHARS or len(context.split()) > MAX_CONTEXT_WORDS:
            return SenseIssue(
                "context_not_a_label",
                f"sense {index} context {context!r} reads as a definition, not a chip",
            )
        if not _is_latin(definition):
            return SenseIssue(
                "definition_not_english",
                f"sense {index} definition is not written in English",
            )
        if not self._native_script_ok(native):
            return SenseIssue(
                "native_meaning_wrong_script",
                f"sense {index} headline is not written in {self._native_language}",
            )
        return None

    def _soft_failures(self, index: int, sense: MeaningSuggestion) -> list[SenseIssue]:
        issues: list[SenseIssue] = []
        definition = sense.definition.strip().casefold()
        example = sense.example.strip()

        if not example:
            issues.append(SenseIssue("missing_example", f"sense {index} has no example sentence"))
        elif example.strip().casefold() == definition:
            issues.append(
                SenseIssue(
                    "example_restates_definition", f"sense {index} example is the definition"
                )
            )
        return issues

    def _native_script_ok(self, text: str) -> bool:
        expected = _EXPECTED_SCRIPTS.get(self._native_language.strip().casefold())
        if expected is None:
            return True
        for char in text:
            if not char.isalpha():
                continue
            try:
                name = unicodedata.name(char)
            except ValueError:
                continue
            if any(name.startswith(prefix) for prefix in expected):
                return True
        return False


def example_mentions_term(example: str, term: str) -> bool:
    """Whether an example plausibly uses the headword.

    Deliberately generous, and deliberately **not** a hard rule. "run" appears in
    an example as "ran"; "be" as "was". A prefix match on the first four
    characters catches ordinary inflection without a stemmer, and the cost of a
    false positive here is one unflagged card, while the cost of a false negative
    is rejecting a perfectly good sense.
    """
    haystack = example.casefold()
    for word in _WORD_RE.findall(term.casefold()):
        if len(word) < 4:
            if word in _WORD_RE.findall(haystack):
                return True
            continue
        if word[:4] in haystack:
            return True
    return False


def _is_latin(text: str) -> bool:
    """Whether the alphabetic characters are Latin script.

    Accents stay in — "café" and "naïve" are ordinary English — so this asks
    about the *script*, not the ASCII range.
    """
    for char in text:
        if not char.isalpha():
            continue
        try:
            if not unicodedata.name(char).startswith("LATIN"):
                return False
        except ValueError:
            continue
    return True
