"""Unit tests for the word_reviews partition maintenance helpers."""

from __future__ import annotations

from datetime import date

import pytest

from app.scripts.partition_word_reviews import _add_months, _partition_month


@pytest.mark.parametrize(
    ("start", "count", "expected"),
    [
        (date(2026, 7, 1), 0, date(2026, 7, 1)),
        (date(2026, 7, 1), 1, date(2026, 8, 1)),
        # year boundaries in both directions — the arithmetic that silently
        # produces a gap in the window if it is wrong
        (date(2026, 12, 1), 1, date(2027, 1, 1)),
        (date(2026, 1, 1), -1, date(2025, 12, 1)),
        (date(2026, 7, 1), 12, date(2027, 7, 1)),
        (date(2026, 7, 1), -24, date(2024, 7, 1)),
        (date(2026, 7, 1), -31, date(2023, 12, 1)),
    ],
)
def test_add_months(start: date, count: int, expected: date) -> None:
    assert _add_months(start, count) == expected


def test_add_months_normalises_to_the_first() -> None:
    assert _add_months(date(2026, 7, 30), 1) == date(2026, 8, 1)


def test_partition_month_parses_generated_names() -> None:
    assert _partition_month("word_reviews_202607") == date(2026, 7, 1)
    assert _partition_month("word_reviews_202601") == date(2026, 1, 1)


@pytest.mark.parametrize(
    "name",
    [
        # The default partition must never be a pruning candidate — dropping it
        # would remove the safety net that keeps inserts from failing.
        "word_reviews_default",
        "word_reviews",
        "word_reviews_2026",
        "word_reviews_20260",
        "some_other_table",
    ],
)
def test_partition_month_ignores_anything_not_a_monthly_partition(name: str) -> None:
    assert _partition_month(name) is None
