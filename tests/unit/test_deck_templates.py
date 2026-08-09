"""Template parsing, and the things it refuses.

Validation runs before anything is spent, which is what makes it worth being
strict here: a structural mistake caught by ``make deck-validate`` costs nothing,
and the same mistake caught after a build costs a deck's worth of provider calls
and a reviewer's afternoon.

Two refusals are load-bearing rather than tidy:

* **Asserted structure.** A word list that silently loses an entry is invisible
  until a learner notices Lesson 12 has eleven words.
* **No room for source prose.** The format has nowhere to paste a definition or
  an example, which is the copyright boundary expressed as a schema.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.entities.deck_template import TemplateError
from app.domain.enums import SenseSelection
from app.infrastructure.templates.deck_template_loader import (
    list_templates,
    load_template,
    validate_template,
)

DECK_YAML = """\
slug: sample
name: Sample Deck
version: "1.2.3"
category: business
description: A deck.
structure:
  words_per_unit: 2
  expected_units: 2
  expected_words: 4
generation:
  native_language: Persian
  sense_selection: [explicit, first]
  enrichment: allowed
"""

WORDS_YAML = """\
units:
  - name: Module 1
    words:
      - term: run
        sense: {pos: verb, context: Management}
      - term: board
        hint: "the group of people who control a company"
  - name: Module 2
    words:
      - margin
      - run
"""


@pytest.fixture
def root(tmp_path: Path) -> Path:
    base = tmp_path / "sample"
    base.mkdir()
    (base / "deck.yaml").write_text(DECK_YAML)
    (base / "words.yaml").write_text(WORDS_YAML)
    return tmp_path


def test_a_template_parses_into_ordered_units_and_words(root: Path) -> None:
    template = load_template("sample", root=root)
    assert template.name == "Sample Deck"
    assert template.version == "1.2.3"
    assert template.unit_count == 2
    assert template.word_count == 4
    assert [u.name for u in template.units] == ["Module 1", "Module 2"]
    assert [w.term for _, _, w in template.words_in_order()] == [
        "run",
        "board",
        "margin",
        "run",
    ]


def test_a_pin_and_a_hint_survive_parsing(root: Path) -> None:
    template = load_template("sample", root=root)
    # First occurrence wins: "run" appears twice, and the pinned one is first.
    words: dict[str, object] = {}
    for _, _, word in template.words_in_order():
        words.setdefault(word.term, word)
    assert words["run"].hint.is_pinned
    assert words["run"].hint.part_of_speech == "verb"
    assert words["board"].hint.gloss.startswith("the group of people")
    assert not words["board"].hint.is_pinned
    assert words["margin"].hint.is_empty


def test_strategies_are_parsed_in_the_order_written(root: Path) -> None:
    template = load_template("sample", root=root)
    assert template.generation.strategies == (SenseSelection.EXPLICIT, SenseSelection.FIRST)


def test_the_hash_changes_when_either_file_changes(root: Path) -> None:
    before = load_template("sample", root=root).content_hash
    (root / "sample" / "words.yaml").write_text(WORDS_YAML.replace("margin", "profit"))
    assert load_template("sample", root=root).content_hash != before


def test_a_word_count_that_disagrees_with_the_declared_structure_is_refused(
    root: Path,
) -> None:
    """The check that catches a list which silently lost an entry."""
    (root / "sample" / "words.yaml").write_text(WORDS_YAML.replace("      - margin\n", ""))
    with pytest.raises(TemplateError, match="structure says 4 words"):
        load_template("sample", root=root)


def test_half_a_pin_is_refused_rather_than_quietly_ignored(root: Path) -> None:
    """The sense key needs both halves; one alone would degrade to "no pin"."""
    (root / "sample" / "words.yaml").write_text(
        WORDS_YAML.replace("sense: {pos: verb, context: Management}", "sense: {pos: verb}")
    )
    with pytest.raises(TemplateError, match="half a sense"):
        load_template("sample", root=root)


def test_a_hint_long_enough_to_be_a_definition_is_refused(root: Path) -> None:
    """The one thing that must never be copied from a source is a definition."""
    (root / "sample" / "words.yaml").write_text(
        WORDS_YAML.replace(
            'hint: "the group of people who control a company"',
            f'hint: "{"x" * 250}"',
        )
    )
    with pytest.raises(TemplateError, match="longer than 200"):
        load_template("sample", root=root)


def test_a_slug_that_disagrees_with_its_directory_is_refused(root: Path) -> None:
    """A copied template whose header was never updated would build under the wrong name."""
    (root / "sample" / "deck.yaml").write_text(DECK_YAML.replace("slug: sample", "slug: other"))
    with pytest.raises(TemplateError, match="declares slug"):
        load_template("sample", root=root)


def test_an_unknown_strategy_is_refused(root: Path) -> None:
    (root / "sample" / "deck.yaml").write_text(
        DECK_YAML.replace("[explicit, first]", "[explicit, vibes]")
    )
    with pytest.raises(TemplateError, match="unknown sense-selection strategy"):
        load_template("sample", root=root)


def test_manual_selection_cannot_be_chosen_by_a_template(root: Path) -> None:
    """`manual` records an admin's override; a build must never award it to itself."""
    (root / "sample" / "deck.yaml").write_text(
        DECK_YAML.replace("[explicit, first]", "[manual, first]")
    )
    with pytest.raises(TemplateError, match="cannot be chosen"):
        load_template("sample", root=root)


def test_a_missing_file_is_refused_by_name(tmp_path: Path) -> None:
    (tmp_path / "lonely").mkdir()
    (tmp_path / "lonely" / "deck.yaml").write_text(
        DECK_YAML.replace("slug: sample", "slug: lonely")
    )
    with pytest.raises(TemplateError, match="words.yaml"):
        load_template("lonely", root=tmp_path)


def test_duplicates_are_reported_but_never_fatal(root: Path) -> None:
    """504 really does repeat words; only the author can say whether that is a bug."""
    report = validate_template(load_template("sample", root=root))
    assert [term for term, _ in report.duplicates] == ["run"]
    assert any("more than once" in w for w in report.warnings)
    assert report.pinned == 1
    assert report.hinted == 1


def test_the_repository_templates_all_parse() -> None:
    """The committed templates are part of the build, so they are part of the suite."""
    slugs = list_templates()
    assert "504-essential-words" in slugs
    for slug in slugs:
        report = validate_template(load_template(slug))
        assert report.template.word_count > 0
