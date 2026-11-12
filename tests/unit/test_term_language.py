"""A declared term language — and the English path it must leave untouched.

Every English assertion here guards money already spent: a changed English
cache key or prompt would retire every card learners have looked up.
"""

from __future__ import annotations

from app.application.ports.ai_service import LearnerContext
from app.application.ports.lookup_cache import build_lookup_cache_key
from app.infrastructure.ai.anthropic_ai_service import AnthropicAIService
from app.infrastructure.ai.openai_compatible_ai_service import OpenAICompatibleAIService
from app.infrastructure.ai.prompts import term_language_note

PERSIAN = LearnerContext(native_language="Persian")
SPANISH = LearnerContext(native_language="Persian", term_language="es")
GERMAN = LearnerContext(native_language="Persian", term_language="de")

#: The digest of ("once", Persian, adult, v2), computed on main before the
#: term language existed. If this moves, every English cache row is orphaned.
ENGLISH_ONCE_DIGEST = "98310e7c779794e28d4414469e3972342c09f8f530aed883c9c486f8954cf96a"


def test_an_english_key_is_the_digest_it_always_was() -> None:
    for learner in (PERSIAN, LearnerContext(native_language="Persian", term_language="en")):
        key = build_lookup_cache_key("Once", learner, 2)
        assert key.digest() == ENGLISH_ONCE_DIGEST
        assert key.for_term("once").digest() == ENGLISH_ONCE_DIGEST


def test_a_declared_language_gets_its_own_key_and_keeps_it_for_the_entry() -> None:
    spanish = build_lookup_cache_key("once", SPANISH, 2)
    german = build_lookup_cache_key("once", GERMAN, 2)
    assert spanish.digest() != ENGLISH_ONCE_DIGEST
    assert spanish.digest() != german.digest()
    assert spanish.for_term("once").digest() == spanish.digest()


def test_the_note_is_empty_for_english() -> None:
    assert term_language_note(PERSIAN) == ""
    assert term_language_note(LearnerContext(term_language="en")) == ""


def test_the_note_names_the_language() -> None:
    assert "Spanish" in term_language_note(SPANISH)
    assert "der Hund" not in term_language_note(SPANISH)
    assert "German" in term_language_note(GERMAN)
    assert "der Hund" in term_language_note(GERMAN)


def _adapters() -> list[object]:
    return [
        AnthropicAIService(api_key="k", model="m"),
        OpenAICompatibleAIService(api_key="k", model="m", base_url="https://gateway.test"),
    ]


def test_both_adapters_tell_the_model_the_language_in_the_user_turn() -> None:
    for adapter in _adapters():
        english = adapter._lookup_user_prompt("Gift", PERSIAN)  # type: ignore[attr-defined]
        german = adapter._lookup_user_prompt("Gift", GERMAN)  # type: ignore[attr-defined]
        assert "German" not in english
        assert "German" in german
        # One line added, nothing else touched.
        assert german.replace(term_language_note(GERMAN), "") == english
