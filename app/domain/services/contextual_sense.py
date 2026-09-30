"""Which stored sense a sentence uses — decided for free where it can be.

The reader's version of :mod:`sense_selection`, with the same two virtues:
**deterministic** (the same sentence and the same senses always pick the same
one) and **free** (no token is spent on a word with one sense, or on a sentence
whose own words say which sense is meant).

When neither free rung answers, the service asks the model what the word means
*in this sentence* — a question that needs no sense list, so it can be asked
alongside the lookup — and :func:`match_meaning` then finds the stored sense
that answer describes, if any. Every rung is reported to the client as
``selection``:

``only``        the lexeme holds one sense — nothing to choose
``overlap``     the sentence's content words cover one sense clearly best
``matched``     the model's meaning for this sentence matched a stored sense
``contextual``  no stored sense matched; the model's meaning itself is shown
``first``       the fallback: the most common sense, as a flashcard shows it
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.application.ports.ai_service import MeaningSuggestion
from app.application.ports.reader_ai import ContextualMeaning
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
    MATCHED = "matched"
    CONTEXTUAL = "contextual"
    FIRST = "first"

    @property
    def is_confident(self) -> bool:
        return self is not ContextualSelection.FIRST


#: How much of the model's definition a stored sense must account for to be
#: the same sense. Lower when the parts of speech agree: that agreement is
#: corroboration the words alone do not carry.
MATCH_MIN_SCORE = 0.5
MATCH_MIN_SCORE_SAME_POS = 0.3


@dataclass(frozen=True, slots=True)
class ContextualChoice:
    #: Index into the sense list the caller passed; ``None`` when the meaning
    #: shown is the model's own rather than a stored sense.
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


def match_meaning(
    meaning: ContextualMeaning, term: str, senses: list[MeaningSuggestion]
) -> ContextualChoice | None:
    """The stored sense the model's meaning describes, or ``None``.

    Coverage is measured *from the model's definition*: a stored sense that
    accounts for most of its content words is the same sense. A meaning whose
    lemma is a longer expression than the looked-up term ("give up" against
    "give") is an idiom the senses cannot hold, and never matches.
    """
    if not senses:
        return None
    lemma = meaning.lemma.strip().casefold()
    if " " in lemma and lemma != term.strip().casefold():
        return None
    wanted = _content_words(f"{meaning.definition} {meaning.context}")
    if not wanted:
        return None
    best: tuple[float, int] | None = None
    for index, sense in enumerate(senses):
        found = _content_words(f"{sense.context} {sense.definition} {sense.example}")
        score = len(wanted & found) / len(wanted)
        same_pos = _same_pos(meaning.part_of_speech, sense.part_of_speech)
        floor = MATCH_MIN_SCORE_SAME_POS if same_pos else MATCH_MIN_SCORE
        if score >= floor and (best is None or score > best[0]):
            best = (score, index)
    if best is None:
        return None
    return ContextualChoice(best[1], ContextualSelection.MATCHED, round(best[0], 3))


def _same_pos(left: str, right: str) -> bool:
    a, b = left.strip().casefold(), right.strip().casefold()
    return bool(a) and a == b


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
