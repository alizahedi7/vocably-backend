"""Which stored sense a sentence uses — decided for free where it can be.

The reader's version of :mod:`sense_selection`, with the same two virtues:
**deterministic** (the same sentence and the same senses always pick the same
one) and **free** (no token is spent on a word with one sense, or on a sentence
whose own words say which sense is meant). Only when the local score is
ambiguous is a model asked, and even then only for an index.

The ladder, first match wins. Every rung is reported to the client as
``selection`` so a screen can hedge ("probably this one") when it should:

``only``     the lexeme holds one sense — nothing to choose
``overlap``  the sentence's content words cover one sense's definition,
             example and label clearly better than any other
``model``    asked, because overlap was silent or tied (done by the service)
``first``    the fallback: the most common sense, as a flashcard shows it
``none``     the model said no listed sense fits; show every sense, flagged
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.application.ports.ai_service import MeaningSuggestion
from app.domain.services.sense_selection import _content_words

#: A sense must cover this share of the sentence's content words to win
#: locally. Low, because a sentence is long and a definition short; the
#: *margin* below is what carries the confidence.
OVERLAP_MIN_SCORE = 0.12
#: The winner must beat the runner-up by this much, or the model decides.
OVERLAP_MIN_MARGIN = 0.08


class ContextualSelection(StrEnum):
    ONLY = "only"
    OVERLAP = "overlap"
    MODEL = "model"
    FIRST = "first"
    NONE = "none"

    @property
    def is_confident(self) -> bool:
        return self in (ContextualSelection.ONLY, ContextualSelection.OVERLAP)


@dataclass(frozen=True, slots=True)
class ContextualChoice:
    #: Index into the sense list the caller passed, or ``None`` for NONE.
    index: int | None
    selection: ContextualSelection
    score: float | None = None


def choose_locally(
    term: str, sentence: str, senses: list[MeaningSuggestion]
) -> ContextualChoice | None:
    """The free rungs. ``None`` means "ask the model, then fall back to first".

    Scores each sense by how much of the sentence's vocabulary it accounts
    for, minus the term itself (which every sense's example contains).
    Coverage is measured *from the sentence*, so a long definition does not
    win merely by mentioning more words.
    """
    if not senses:
        return None
    if len(senses) == 1:
        return ContextualChoice(0, ContextualSelection.ONLY, 1.0)

    wanted = _content_words(sentence) - _content_words(term)
    if len(wanted) < 3:
        return None  # Too little context to say anything; the model reads it better.

    scored: list[tuple[float, int]] = []
    for index, sense in enumerate(senses):
        haystack = _content_words(f"{sense.context} {sense.definition} {sense.example}")
        scored.append((len(wanted & haystack) / len(wanted), index))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    (best, best_index), (runner_up, _) = scored[0], scored[1]
    if best >= OVERLAP_MIN_SCORE and best - runner_up >= OVERLAP_MIN_MARGIN:
        return ContextualChoice(best_index, ContextualSelection.OVERLAP, round(best, 3))
    return None


def sentence_around(text: str, start: int, end: int, *, max_chars: int = 400) -> str:
    """The sentence in ``text`` containing ``[start, end)``, bounded.

    A deliberately naive splitter — a full stop, question or exclamation mark
    followed by a space — because the input is edited prose, and the cost of
    an over-long sentence is a few tokens, where a dependency for this would be
    a dependency for this.
    """
    if not text or start < 0 or end > len(text) or start >= end:
        return text[:max_chars]
    lo = start
    while lo > 0 and not (text[lo - 1] in ".!?" and text[lo] == " "):
        lo -= 1
    hi = end
    while hi < len(text) and not (text[hi - 1] in ".!?" and text[hi] == " "):
        hi += 1
    sentence = text[lo:hi].strip()
    if len(sentence) <= max_chars:
        return sentence
    # Keep the window centred on the tap when the "sentence" is a page long.
    centre = (start + end) // 2
    return text[max(0, centre - max_chars // 2) : centre + max_chars // 2].strip()
