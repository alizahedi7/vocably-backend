"""The id a client rates an AI card back by.

Three rules, and each one is a way ratings would otherwise be counted against
the wrong thing:

* it is keyed on the **resolved** term, so a typo and its correction rate one deck;
* it is **empty when there is nothing to rate**, so no client renders a control
  that would post about a deck that does not exist;
* it changes with the **prompt version**, so a verdict on the old cards is never
  counted against the new ones.

Exercised at the service rather than through the API because the stub provider
answers every input with a sense — the ``unsupported`` path it must handle is not
reachable from the endpoint without the caching harness in
``tests/api/test_ai_lookup_cache.py``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from app.application.ports.ai_service import (
    LearnerContext,
    LookupResult,
    LookupStatus,
    MeaningSuggestion,
)
from app.application.ports.lookup_cache import build_lookup_cache_key
from app.application.services.ai_studio_service import AIStudioService
from app.domain.entities.user import User

PROMPT_VERSION = 7


class _FixedAI:
    """Answers with whatever it was handed, ignoring the input."""

    def __init__(self, result: LookupResult) -> None:
        self.result = result

    async def look_up_meanings(self, term: str, learner: LearnerContext) -> LookupResult:
        return self.result

    async def generate_story(self, words: list[str], learner: LearnerContext) -> Any:
        raise NotImplementedError


class _OneUser:
    def __init__(self, user: User) -> None:
        self._user = user

    async def get(self, user_id: UUID) -> User:
        return self._user


def _service(result: LookupResult, *, native_language: str = "Persian") -> AIStudioService:
    user = User(id=uuid4(), native_language=native_language)
    return AIStudioService(
        _FixedAI(result),  # type: ignore[arg-type]
        progress=None,  # type: ignore[arg-type]
        users=_OneUser(user),  # type: ignore[arg-type]
        prompt_version=PROMPT_VERSION,
    )


def _sense() -> MeaningSuggestion:
    return MeaningSuggestion(native_meaning="دویدن", definition="to move quickly", example="I run.")


async def test_the_id_is_keyed_on_the_resolved_term_not_the_typed_one() -> None:
    """A typo and its correction rate the same deck, because they *are* the
    same deck — the cache stores its senses under the resolved term too."""
    corrected = LookupResult(
        term="receive",
        suggestions=[_sense()],
        status=LookupStatus.CORRECTED,
        notice="Showing results for “receive”.",
    )
    service = _service(corrected)
    typo = await service.look_up_meanings(uuid4(), "recieve")
    clean = await service.look_up_meanings(uuid4(), "receive")
    assert typo.lookup_id == clean.lookup_id
    assert typo.lookup_id != ""


async def test_an_unsupported_lookup_has_no_id() -> None:
    """Empty means "no rating control" — the same rule the client follows for an
    empty ``phonetic``, and never an error."""
    service = _service(
        LookupResult(term="", suggestions=[], status=LookupStatus.UNSUPPORTED, notice="Hmm.")
    )
    view = await service.look_up_meanings(uuid4(), "qwrtyp")
    assert view.lookup_id == ""
    assert view.result.suggestions == []


async def test_the_id_matches_the_cache_key_for_the_same_lookup() -> None:
    """It *is* ``ai_lookup_entries.entry_hash``, which is what lets a rating be
    joined back to the text that was rated."""
    result = LookupResult(term="run", suggestions=[_sense()])
    view = await _service(result).look_up_meanings(uuid4(), "run")
    expected = build_lookup_cache_key(
        "run", LearnerContext(native_language="Persian"), PROMPT_VERSION
    ).digest()
    assert view.lookup_id == expected


async def test_a_new_prompt_version_is_a_new_id() -> None:
    """A new prompt writes different cards. A verdict on the old ones must not
    be counted against the new."""
    result = LookupResult(term="run", suggestions=[_sense()])
    learner = LearnerContext(native_language="Persian")
    first = build_lookup_cache_key("run", learner, PROMPT_VERSION).digest()
    second = build_lookup_cache_key("run", learner, PROMPT_VERSION + 1).digest()
    assert first != second
    view = await _service(result).look_up_meanings(uuid4(), "run")
    assert view.lookup_id == first


async def test_two_native_languages_are_two_decks() -> None:
    """The headline is written in the learner's own language, so the cards a
    Persian speaker rates are not the cards a Spanish speaker saw."""
    result = LookupResult(term="run", suggestions=[_sense()])
    persian = await _service(result, native_language="Persian").look_up_meanings(uuid4(), "run")
    spanish = await _service(result, native_language="Spanish").look_up_meanings(uuid4(), "run")
    assert persian.lookup_id != spanish.lookup_id


@pytest.mark.parametrize("typed", ["run", "  RUN  ", "run."])
async def test_incidental_typing_does_not_split_the_score(typed: str) -> None:
    """Case and stray punctuation are accidents of typing, so they collide —
    the same normalisation the cache key applies."""
    result = LookupResult(term=typed.strip(" ."), suggestions=[_sense()])
    view = await _service(result).look_up_meanings(uuid4(), typed)
    canonical = build_lookup_cache_key(
        "run", LearnerContext(native_language="Persian"), PROMPT_VERSION
    ).digest()
    assert view.lookup_id == canonical
