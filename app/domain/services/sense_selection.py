"""Choosing which one of a word's senses a pre-built deck shows.

The shared lexicon holds every useful sense of *run*; the *504* deck shows one of
them and *Business English* shows another. This module is that choice, and it has
two properties worth defending:

**Deterministic.** The same lexicon and the same template always pick the same
sense. A build that silently disagreed with itself between runs would make the
deck un-reviewable — a reviewer approves cards, not dice.

**Free.** Strategies 1–4 cost no tokens. Asking a model which sense a deck wants
is possible (``SenseSelection.AI``) but opt-in per template and batched, because
paying per word to rank senses we already own would undo most of what the lexicon
saves.

The order is a confidence ladder, and every rung is recorded on the item:
an explicit pin needs no human eye, a first-sense fallback probably does. That
recording is what turns "review 504 cards" into "review the nineteen the pipeline
was unsure about".
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.entities.deck_build import SenseHint
from app.domain.entities.lexeme import LexemeSense, sense_key_for
from app.domain.enums import SenseSelection

#: How well a hint must match before it beats the category prior. Tuned so that
#: "to be in charge of a company" matches "to control or be in charge of a
#: business" and does not match "to move quickly on foot".
HINT_MIN_SCORE = 0.35

#: How well a category prior must match before it beats "take the first sense".
#: Higher than the hint threshold on purpose: a category is a much weaker signal
#: than words the template author actually wrote, and a wrong confident pick is
#: worse than an honest fallback.
CATEGORY_MIN_SCORE = 0.5

#: Words that carry no selective power. Not a general stopword list — just the
#: glue that appears in every definition and would otherwise let any hint match
#: anything.
_STOPWORD_TEXT = """
    a an the of to in on at by for with from into over under and or but not no
    is are was were be been being am do does did doing have has had having
    it its this that these those as if then than so such your you his her their
    something someone somebody anything anyone one who which what when where how
    very much many more most some any each other another same able way ways
"""
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())

#: Context labels and definition words each deck category prefers. Deliberately
#: small and hand-written: a category is a coarse prior, and a long generated
#: list would fire on words that merely mention money or school.
CATEGORY_PRIORS: dict[str, tuple[str, ...]] = {
    "business": (
        "business",
        "company",
        "commerce",
        "finance",
        "management",
        "manage",
        "money",
        "trade",
        "market",
        "employee",
        "contract",
        "profit",
        "office",
        "negotiation",
    ),
    "academic": (
        "academic",
        "research",
        "study",
        "analysis",
        "theory",
        "evidence",
        "argument",
        "science",
        "scientific",
        "method",
        "university",
    ),
    "exam": (),
    "travel": ("travel", "journey", "transport", "hotel", "tourist", "flight", "luggage"),
    "technology": ("computer", "software", "digital", "internet", "device", "data", "machine"),
    "medical": ("medical", "medicine", "disease", "patient", "treatment", "body", "health"),
    "general": (),
}


@dataclass(frozen=True, slots=True)
class SenseChoice:
    sense: LexemeSense
    strategy: SenseSelection
    #: 0..1 for the scoring strategies, ``None`` where the choice was categorical.
    score: float | None = None

    @property
    def needs_review(self) -> bool:
        return not self.strategy.is_confident


class SenseSelector:
    """Runs the strategy chain a template configured, first match wins."""

    def __init__(
        self,
        *,
        strategies: tuple[SenseSelection, ...],
        category: str = "general",
        hint_min_score: float = HINT_MIN_SCORE,
        category_min_score: float = CATEGORY_MIN_SCORE,
    ) -> None:
        self._strategies = strategies
        self._category = category
        self._hint_min = hint_min_score
        self._category_min = category_min_score

    def select(self, senses: list[LexemeSense], hint: SenseHint) -> SenseChoice | None:
        """The sense this deck should show, or ``None`` if nothing qualifies.

        ``None`` is a real answer and the caller acts on it: for an item that has
        not been enriched yet it means "ask for the missing sense", and after
        enrichment it means "write the card and flag it for a human".
        """
        if not senses:
            return None
        for strategy in self._strategies:
            choice = self._apply(strategy, senses, hint)
            if choice is not None:
                return choice
        return None

    def _apply(
        self,
        strategy: SenseSelection,
        senses: list[LexemeSense],
        hint: SenseHint,
    ) -> SenseChoice | None:
        if strategy is SenseSelection.EXPLICIT:
            return self._explicit(senses, hint)
        if strategy is SenseSelection.HINT:
            return self._by_hint(senses, hint)
        if strategy is SenseSelection.CATEGORY:
            return self._by_category(senses)
        if strategy is SenseSelection.FIRST:
            return SenseChoice(senses[0], SenseSelection.FIRST)
        # AI ranking is applied by the service before the chain runs; MANUAL only
        # ever arrives from an admin. Neither is selectable here.
        return None

    def _explicit(self, senses: list[LexemeSense], hint: SenseHint) -> SenseChoice | None:
        if not hint.is_pinned:
            return None
        wanted = sense_key_for(hint.part_of_speech, hint.context)
        for sense in senses:
            if sense.sense_key == wanted:
                return SenseChoice(sense, SenseSelection.EXPLICIT, 1.0)
        # A pin that matches nothing is not a reason to guess: fall through to
        # the next strategy, and let enrichment try to produce what was asked for.
        return None

    def _by_hint(self, senses: list[LexemeSense], hint: SenseHint) -> SenseChoice | None:
        wanted = _content_words(hint.gloss)
        if not wanted:
            return None
        best: SenseChoice | None = None
        for sense in senses:
            score = _coverage(wanted, _content_words(f"{sense.context} {sense.definition}"))
            if hint.part_of_speech and _same_pos(hint.part_of_speech, sense.part_of_speech):
                # A part-of-speech agreement is corroboration, not a match on its
                # own — enough to break a tie, never enough to clear the floor.
                score = min(score + 0.15, 1.0)
            if score >= self._hint_min and (best is None or score > (best.score or 0)):
                best = SenseChoice(sense, SenseSelection.HINT, round(score, 3))
        return best

    def _by_category(self, senses: list[LexemeSense]) -> SenseChoice | None:
        priors = CATEGORY_PRIORS.get(self._category.strip().casefold())
        if not priors:
            return None
        wanted = set(priors)
        best: SenseChoice | None = None
        for sense in senses:
            haystack = _content_words(f"{sense.context} {sense.definition} {sense.example}")
            hits = len(wanted & haystack)
            if not hits:
                continue
            # Scored against a small fixed vocabulary, so normalise by a constant
            # rather than by the prior's length: two category words in one
            # definition is already a strong signal and three is conclusive.
            score = min(hits / 2, 1.0)
            if score >= self._category_min and (best is None or score > (best.score or 0)):
                best = SenseChoice(sense, SenseSelection.CATEGORY, round(score, 3))
        return best


def _content_words(text: str) -> set[str]:
    return {
        word
        for word in "".join(c if c.isalnum() else " " for c in text.casefold()).split()
        if len(word) > 2 and word not in _STOPWORDS
    }


def _coverage(wanted: set[str], found: set[str]) -> float:
    """Share of the hint's content words the sense accounts for.

    Coverage rather than Jaccard: a definition is usually longer than a hint, and
    a symmetric measure would punish a sense for being thorough. What matters is
    whether the sense says what the hint asked about.
    """
    if not wanted:
        return 0.0
    return len(wanted & found) / len(wanted)


def _same_pos(left: str, right: str) -> bool:
    return left.strip().casefold() == right.strip().casefold()
