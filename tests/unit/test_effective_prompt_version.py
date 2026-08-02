"""Grounded and generated cards must never share a lookup-cache key.

They read differently — one selects and translates a dictionary entry, the other
recalls senses from the model. If both landed under the same key, flipping
``DICTIONARY_ENABLED`` would serve a mix of the two, and rolling the flag back
would leave the cards written while it was on in place, invisibly.
"""

from __future__ import annotations

import pytest

from app.api.deps import _effective_prompt_version
from app.core.config import settings
from app.infrastructure.ai.prompts import PROMPT_VERSION
from app.infrastructure.ai.translate_prompts import TRANSLATE_PROMPT_VERSION


@pytest.fixture
def dictionary_enabled(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    def _set(enabled: bool) -> None:
        monkeypatch.setattr(settings, "dictionary_enabled", enabled)

    return _set


def test_generative_pipeline_keeps_the_plain_prompt_version(dictionary_enabled) -> None:  # type: ignore[no-untyped-def]
    """Turning grounding off must reuse every card cached before it existed."""
    dictionary_enabled(False)
    assert _effective_prompt_version() == PROMPT_VERSION


def test_grounded_pipeline_uses_a_distinct_version(dictionary_enabled) -> None:  # type: ignore[no-untyped-def]
    dictionary_enabled(True)
    assert _effective_prompt_version() != PROMPT_VERSION


def test_both_prompt_versions_are_recoverable_from_the_key(dictionary_enabled) -> None:  # type: ignore[no-untyped-def]
    """Either prompt can be bumped independently without colliding."""
    dictionary_enabled(True)
    combined = _effective_prompt_version() // 10

    assert combined % 1000 == TRANSLATE_PROMPT_VERSION
    assert combined // 1000 == PROMPT_VERSION


def test_the_two_grounded_modes_do_not_share_a_key(  # type: ignore[no-untyped-def]
    dictionary_enabled,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """They write visibly different card fronts, so they must not mix in cache."""
    dictionary_enabled(True)

    monkeypatch.setattr(settings, "dictionary_rewrite_definitions", False)
    translate_only = _effective_prompt_version()
    monkeypatch.setattr(settings, "dictionary_rewrite_definitions", True)
    rewrite = _effective_prompt_version()

    assert translate_only != rewrite
    assert PROMPT_VERSION not in (translate_only, rewrite)
