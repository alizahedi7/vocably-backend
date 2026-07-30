"""The lookup cache key — what collides, and what deliberately does not.

Every assertion here is a cost or correctness decision, not a formatting detail:
under-normalising re-buys entries that should have collided, over-normalising
serves the wrong card, and a key that ignores something the prompt varies on
serves text written for somebody else.
"""

from __future__ import annotations

from app.application.ports.ai_service import LearnerContext
from app.application.ports.lookup_cache import (
    MAX_ALIAS_INPUT_CHARS,
    CacheAgeBucket,
    build_lookup_cache_key,
    normalize_lookup_input,
)
from app.domain.enums import AgeRange


def _key(term: str, **learner: object) -> object:
    context = LearnerContext(**learner)  # type: ignore[arg-type]
    return build_lookup_cache_key(term, context, prompt_version=1).digest()


# ── Normalisation ────────────────────────────────────────────
def test_case_and_surrounding_noise_collide() -> None:
    assert normalize_lookup_input("  Run  ") == "run"
    assert normalize_lookup_input("“run”") == "run"
    assert normalize_lookup_input("run.") == "run"
    assert normalize_lookup_input("RUN!") == "run"


def test_internal_whitespace_is_collapsed_not_stripped() -> None:
    assert normalize_lookup_input("give   up") == "give up"


def test_unicode_forms_of_the_same_text_collide() -> None:
    # NFKC: composed vs decomposed é must not buy two entries.
    assert normalize_lookup_input("café") == normalize_lookup_input("café")


def test_inflections_are_not_merged() -> None:
    """No stemming — "running" and "run" are different cards, not a collision."""
    assert normalize_lookup_input("running") != normalize_lookup_input("run")


# ── Key composition ──────────────────────────────────────────
def test_native_language_changes_the_key() -> None:
    """`native_meaning` is written in it, so entries must not be shared."""
    assert _key("run", native_language="English") != _key("run", native_language="Persian")


def test_interests_do_not_change_the_key() -> None:
    """The deliberate trade: themed examples would collapse the hit rate."""
    assert _key("run", interests=("travel",)) == _key("run", interests=("cooking",))
    assert _key("run", interests=("travel",)) == _key("run")


def test_adult_age_ranges_share_one_bucket() -> None:
    """Eight demographic buckets, three that change the card's text."""
    assert _key("run", age_range=AgeRange.YOUNG_ADULT.value) == _key(
        "run", age_range=AgeRange.ADULT_45.value
    )
    assert _key("run", age_range=AgeRange.UNDER_13.value) != _key(
        "run", age_range=AgeRange.ADULT_45.value
    )
    assert _key("run", age_range=AgeRange.TEEN.value) != _key(
        "run", age_range=AgeRange.UNDER_13.value
    )


def test_unknown_age_range_falls_back_to_adult() -> None:
    assert CacheAgeBucket.from_age_range("Martian") is CacheAgeBucket.ADULT
    assert CacheAgeBucket.from_age_range(None) is CacheAgeBucket.ADULT
    assert CacheAgeBucket.from_age_range(AgeRange.PREFER_NOT_TO_SHARE.value) is CacheAgeBucket.ADULT


def test_prompt_version_retires_previous_entries() -> None:
    learner = LearnerContext()
    v1 = build_lookup_cache_key("run", learner, prompt_version=1)
    v2 = build_lookup_cache_key("run", learner, prompt_version=2)
    assert v1.digest() != v2.digest()


def test_entry_key_is_shared_by_every_input_that_resolves_to_the_term() -> None:
    """A typo and the correct spelling must buy one entry, not two."""
    learner = LearnerContext()
    typo = build_lookup_cache_key("runing", learner, prompt_version=1)
    clean = build_lookup_cache_key("Run", learner, prompt_version=1)
    assert typo.digest() != clean.digest()
    assert typo.for_term("run").digest() == clean.for_term("run").digest()


# ── Alias eligibility ────────────────────────────────────────
def test_short_input_is_aliasable_and_long_input_is_not() -> None:
    learner = LearnerContext()
    assert build_lookup_cache_key("run", learner, prompt_version=1).is_aliasable
    sentence = "x" * (MAX_ALIAS_INPUT_CHARS + 1)
    assert not build_lookup_cache_key(sentence, learner, prompt_version=1).is_aliasable
    assert not build_lookup_cache_key("   ", learner, prompt_version=1).is_aliasable
