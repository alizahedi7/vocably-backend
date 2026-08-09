"""Which sense a pre-built deck shows, and how confident that choice was.

Two properties matter more than any individual pick:

* **Determinism** — the same lexicon and template always choose the same sense.
  A reviewer approves cards, not dice.
* **Honest confidence** — a fallback must record itself as one. That recording is
  what turns "review 504 cards" into "review the nineteen we were unsure about",
  which is the difference between review happening and not.
"""

from __future__ import annotations

from app.domain.entities.deck_build import SenseHint
from app.domain.entities.lexeme import LexemeSense, sense_key_for
from app.domain.enums import SenseSelection
from app.domain.services.sense_selection import SenseSelector

STRATEGIES = (
    SenseSelection.EXPLICIT,
    SenseSelection.HINT,
    SenseSelection.CATEGORY,
    SenseSelection.FIRST,
)


def make_sense(pos: str, context: str, definition: str, position: int = 0) -> LexemeSense:
    return LexemeSense(
        sense_key=sense_key_for(pos, context),
        part_of_speech=pos,
        context=context,
        definition=definition,
        position=position,
    )


MOVEMENT = make_sense("verb", "Movement", "to move quickly on foot", 0)
MANAGEMENT = make_sense("verb", "Management", "to control or be in charge of a business", 1)
PERIOD = make_sense("noun", "Period", "a continuous series of performances", 2)
RUN_SENSES = [MOVEMENT, MANAGEMENT, PERIOD]


def selector(category: str = "general") -> SenseSelector:
    return SenseSelector(strategies=STRATEGIES, category=category)


def test_an_explicit_pin_wins_outright_and_needs_no_review() -> None:
    choice = selector().select(RUN_SENSES, SenseHint(part_of_speech="verb", context="Management"))
    assert choice is not None
    assert choice.sense is MANAGEMENT
    assert choice.strategy is SenseSelection.EXPLICIT
    assert not choice.needs_review


def test_a_pin_that_matches_nothing_falls_through_rather_than_guessing() -> None:
    """The sense asked for is absent — that is enrichment's cue, not a reason to pick."""
    choice = selector().select(RUN_SENSES, SenseHint(part_of_speech="noun", context="Sequence"))
    assert choice is not None
    assert choice.strategy is SenseSelection.FIRST


def test_a_hint_matches_the_sense_it_describes() -> None:
    choice = selector().select(RUN_SENSES, SenseHint(gloss="to be in charge of a company"))
    assert choice is not None
    assert choice.sense is MANAGEMENT
    assert choice.strategy is SenseSelection.HINT
    assert choice.score is not None and choice.score >= 0.35


def test_a_hint_that_describes_nothing_stored_does_not_force_a_match() -> None:
    choice = selector().select(RUN_SENSES, SenseHint(gloss="a colour between red and yellow"))
    assert choice is not None
    assert choice.strategy is SenseSelection.FIRST
    assert choice.needs_review


def test_the_category_prior_prefers_the_business_sense() -> None:
    choice = selector("business").select(RUN_SENSES, SenseHint())
    assert choice is not None
    assert choice.sense is MANAGEMENT
    assert choice.strategy is SenseSelection.CATEGORY


def test_a_category_with_no_prior_falls_back_to_the_first_sense() -> None:
    """`exam` has no prior on purpose: a course teaches the common sense."""
    choice = selector("exam").select(RUN_SENSES, SenseHint())
    assert choice is not None
    assert choice.sense is MOVEMENT
    assert choice.strategy is SenseSelection.FIRST


def test_the_first_sense_fallback_is_marked_for_review() -> None:
    choice = selector().select(RUN_SENSES, SenseHint())
    assert choice is not None
    assert choice.needs_review


def test_selection_is_deterministic_across_repeated_calls() -> None:
    picks = [selector("business").select(RUN_SENSES, SenseHint()) for _ in range(5)]
    assert all(p is not None for p in picks)
    assert len({(p.sense.id, p.strategy, p.score) for p in picks if p}) == 1


def test_an_empty_lexeme_selects_nothing() -> None:
    """`None` is a real answer: it is what triggers enrichment."""
    assert selector().select([], SenseHint(gloss="anything")) is None


def test_a_chain_without_a_fallback_can_decline_to_choose() -> None:
    """A template may configure "pin or nothing" — and then nothing is correct."""
    strict = SenseSelector(strategies=(SenseSelection.EXPLICIT,), category="general")
    assert strict.select(RUN_SENSES, SenseHint(gloss="anything")) is None


def test_part_of_speech_agreement_breaks_a_tie_but_cannot_clear_the_floor() -> None:
    """Corroboration, not evidence: a matching POS alone must not select a sense."""
    choice = selector().select(RUN_SENSES, SenseHint(part_of_speech="verb", gloss="zzz qqq"))
    assert choice is not None
    assert choice.strategy is SenseSelection.FIRST
