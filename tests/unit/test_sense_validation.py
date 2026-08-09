"""The quality gate between a provider and the shared lexicon.

The distinction under test is severity, not correctness: a rejection means the
sense cannot be a card, a warning means it probably can and a human should look.
Getting that boundary wrong in either direction is expensive — reject too much
and a 504-word build stops on an example sentence, warn too much and the review
queue becomes the whole deck.
"""

from __future__ import annotations

from app.application.ports.ai_service import MeaningSuggestion
from app.domain.services.sense_validation import (
    SenseValidator,
    example_mentions_term,
)


def sense(**overrides: str) -> MeaningSuggestion:
    base = {
        "native_meaning": "دویدن",
        "definition": "to move using your legs, faster than walking",
        "example": "I run every morning.",
        "context": "Movement",
        "part_of_speech": "verb",
    }
    base.update(overrides)
    return MeaningSuggestion(**base)  # type: ignore[arg-type]


def validator(native_language: str = "Persian", max_senses: int = 4) -> SenseValidator:
    return SenseValidator(native_language=native_language, max_senses=max_senses)


def test_a_good_sense_passes_clean() -> None:
    outcome = validator().validate([sense()])
    assert len(outcome.accepted) == 1
    assert not outcome.rejected
    assert not outcome.warnings
    assert not outcome.needs_review


def test_an_empty_definition_is_rejected_not_flagged() -> None:
    """A card with no definition cannot be rendered, so it never becomes content."""
    outcome = validator().validate([sense(definition="   ")])
    assert outcome.is_empty
    assert outcome.rejected[0].code == "empty_definition"


def test_a_headline_written_in_english_is_rejected() -> None:
    """The failure that survives schema validation and looks fine in a log.

    The model was told to translate and answered in English. Everything about the
    payload is well-formed; the card is useless.
    """
    outcome = validator().validate([sense(native_meaning="to run")])
    assert outcome.is_empty
    assert outcome.rejected[0].code == "native_meaning_wrong_script"


def test_a_language_with_no_declared_script_is_not_script_checked() -> None:
    """Absent a known expectation, guessing would reject perfectly good output."""
    outcome = validator("Swahili").validate([sense(native_meaning="kukimbia")])
    assert len(outcome.accepted) == 1


def test_a_part_of_speech_that_is_a_sentence_is_rejected() -> None:
    outcome = validator().validate([sense(part_of_speech="this word is used as an action word")])
    assert outcome.rejected[0].code == "invalid_part_of_speech"


def test_a_context_that_reads_as_a_definition_is_rejected() -> None:
    """`context` is a chip on the card back, not a second definition."""
    outcome = validator().validate([sense(context="when you move quickly on foot for exercise")])
    assert outcome.rejected[0].code == "context_not_a_label"


def test_two_senses_sharing_a_key_keep_only_the_first() -> None:
    """A duplicate is the model losing track of its own list; only one is trusted."""
    outcome = validator().validate(
        [
            sense(),
            sense(definition="to jog", native_meaning="دویدن آرام"),
        ]
    )
    assert len(outcome.accepted) == 1
    assert outcome.rejected[0].code == "duplicate_sense"


def test_two_senses_sharing_an_example_keep_only_the_first() -> None:
    outcome = validator().validate(
        [
            sense(),
            sense(context="Management", native_meaning="اداره کردن", definition="to manage"),
        ]
    )
    assert len(outcome.accepted) == 1
    assert outcome.rejected[0].code == "duplicate_example"


def test_more_senses_than_the_cap_are_truncated_not_rejected() -> None:
    """Being given more good senses than a card deck renders is not an error."""
    outcome = validator(max_senses=2).validate(
        [
            sense(context="Movement"),
            sense(context="Management", example="She runs a bakery."),
            sense(context="Period", example="a run of bad luck"),
        ]
    )
    assert len(outcome.accepted) == 2
    assert not outcome.rejected


def test_a_missing_example_is_a_warning_and_the_sense_still_stores() -> None:
    """A build of five hundred words must not stop over one missing sentence."""
    outcome = validator().validate([sense(example="")])
    assert len(outcome.accepted) == 1
    assert outcome.needs_review
    assert outcome.warnings[0].code == "missing_example"


def test_an_example_that_restates_the_definition_is_flagged() -> None:
    outcome = validator().validate([sense(example="to move using your legs, faster than walking")])
    assert len(outcome.accepted) == 1
    assert outcome.warnings[0].code == "example_restates_definition"


def test_example_mentions_term_tolerates_inflection() -> None:
    """Deliberately generous: rejecting a good sense costs more than not flagging one."""
    assert example_mentions_term("She ran to the shop.", "run") is False
    assert example_mentions_term("He is running late.", "running") is True
    assert example_mentions_term("They abandoned the plan.", "abandon") is True
    assert example_mentions_term("Nothing to do with it.", "abandon") is False
