"""Phonetic must survive the cache, or a hit silently downgrades the card.

The codec is versioned separately from the prompts (``SCHEMA_VERSION`` covers
storage shape, ``PROMPT_VERSION`` covers content), so adding a field to the card
is exactly the kind of change that can round-trip wrong without anything else
failing.
"""

from __future__ import annotations

from app.application.ports.ai_service import MeaningSuggestion
from app.infrastructure.db import lookup_cache_payload


def _senses() -> list[MeaningSuggestion]:
    return [
        MeaningSuggestion(
            native_meaning="تضعیف کردن",
            definition="to weaken someone's authority gradually",
            example="His remarks undermined her position.",
            context="Power",
            part_of_speech="verb",
        )
    ]


def test_phonetic_round_trips() -> None:
    decoded = lookup_cache_payload.decode(lookup_cache_payload.encode(_senses(), "/ʌndəˈmaɪn/"))

    assert decoded is not None
    assert decoded.phonetic == "/ʌndəˈmaɪn/"
    assert decoded.suggestions[0].native_meaning == "تضعیف کردن"


def test_absent_phonetic_round_trips_as_empty() -> None:
    """The generated path has no IPA; that must not read back as a failure."""
    decoded = lookup_cache_payload.decode(lookup_cache_payload.encode(_senses()))

    assert decoded is not None
    assert decoded.phonetic == ""
    assert len(decoded.suggestions) == 1


def test_rows_written_before_phonetic_existed_are_a_miss() -> None:
    """An older row lacks the field; re-fetching beats serving a partial card."""
    stale = lookup_cache_payload.encode(_senses(), "/x/")
    stale["v"] = lookup_cache_payload.SCHEMA_VERSION - 1

    assert lookup_cache_payload.decode(stale) is None
