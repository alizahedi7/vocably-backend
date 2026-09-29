"""The free rungs of choosing a sentence's sense, and cutting the sentence out.

Everything here must be decided without a model: a word with one sense, and a
sentence whose own words say which sense it means. Anything short of a clear
winner must defer — a confident wrong pick is worse than asking.
"""

from __future__ import annotations

from app.application.ports.ai_service import MeaningSuggestion
from app.domain.services.contextual_sense import (
    ContextualSelection,
    choose_locally,
    sentence_around,
)

FINANCE = MeaningSuggestion(
    "بانک",
    "an organization that keeps and lends money",
    "I paid the cheque into the bank.",
    "Finance",
    "noun",
)
RIVER = MeaningSuggestion(
    "ساحل",
    "the land along the side of a river",
    "They sat on the river bank fishing.",
    "River",
    "noun",
)


def test_one_sense_needs_no_choosing() -> None:
    choice = choose_locally("bank", "", [FINANCE])
    assert choice is not None
    assert (choice.index, choice.selection) == (0, ContextualSelection.ONLY)
    assert choice.selection.is_confident


def test_a_sentence_that_names_its_sense_is_decided_for_free() -> None:
    choice = choose_locally(
        "bank",
        "She paid her salary cheque into the bank to keep the money safe.",
        [FINANCE, RIVER],
    )
    assert choice is not None
    assert (choice.index, choice.selection) == (0, ContextualSelection.OVERLAP)
    choice = choose_locally(
        "bank", "Alice sat down by the river bank, fishing all afternoon.", [FINANCE, RIVER]
    )
    assert choice is not None and choice.index == 1


def test_a_sentence_with_no_shared_vocabulary_defers_to_the_model() -> None:
    assert (
        choose_locally(
            "bank", "She walked slowly towards the bank without saying anything.", [FINANCE, RIVER]
        )
        is None
    )


def test_a_tie_defers_rather_than_guessing() -> None:
    # "money" favours one sense and "river" the other, by the same amount.
    assert (
        choose_locally(
            "bank", "The money was hidden somewhere near the river bank.", [FINANCE, RIVER]
        )
        is None
    )


def test_too_little_context_defers() -> None:
    assert choose_locally("bank", "The bank.", [FINANCE, RIVER]) is None
    assert choose_locally("bank", "", [FINANCE, RIVER]) is None


def test_no_senses_is_no_choice() -> None:
    assert choose_locally("bank", "Anything at all here, really.", []) is None


def test_the_sentence_around_a_tap_is_cut_at_sentence_ends() -> None:
    text = "It rained. She went to the bank to pay. Then she came home."
    start = text.index("bank")
    assert sentence_around(text, start, start + 4) == "She went to the bank to pay."


def test_the_first_and_last_sentences_reach_the_edges() -> None:
    text = "Down went Alice. Would the fall never come to an end?"
    assert sentence_around(text, 5, 9) == "Down went Alice."
    assert sentence_around(text, len(text) - 4, len(text) - 1) == (
        "Would the fall never come to an end?"
    )


def test_offsets_that_do_not_fit_return_the_start_of_the_text() -> None:
    assert sentence_around("Short text.", 20, 25) == "Short text."
    assert sentence_around("Short text.", 3, 3) == "Short text."


def test_a_page_long_sentence_is_windowed_around_the_tap() -> None:
    text = "word " * 400 + "bank " + "word " * 400
    start = text.index("bank")
    window = sentence_around(text, start, start + 4, max_chars=100)
    assert "bank" in window and len(window) <= 100
