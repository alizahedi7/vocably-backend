"""The grounded lookup path, and every way it must get out of the way.

The interesting cases are not "the dictionary worked". They are the ones where
it did not: a miss, a timeout, a rate limit, a translator that raised, a
translator that returned nothing usable. Each must land on full generation, and
none may reach the learner as an error — the dictionary is an optimisation in
front of a path that already answers every input.
"""

from __future__ import annotations

import pytest

from app.application.ports.ai_service import (
    AIService,
    GeneratedStory,
    LearnerContext,
    LookupResult,
    LookupStatus,
    MeaningSuggestion,
)
from app.application.ports.dictionary_service import (
    DictionaryEntry,
    DictionarySense,
    DictionaryService,
)
from app.infrastructure.ai.grounded_ai_service import GroundedAIService

LEARNER = LearnerContext(native_language="Persian", age_range="25-34", interests=("travel",))


class _RecordingProvider(AIService):
    """Stands in for the real adapter; records whether generation was reached."""

    def __init__(self) -> None:
        self.lookup_calls: list[str] = []
        self.story_calls: list[list[str]] = []

    async def look_up_meanings(self, term: str, learner: LearnerContext) -> LookupResult:
        self.lookup_calls.append(term)
        return LookupResult(
            term=term,
            suggestions=[MeaningSuggestion(native_meaning="generated", definition="generated")],
            status=LookupStatus.OK,
        )

    async def generate_story(self, words: list[str], learner: LearnerContext) -> GeneratedStory:
        self.story_calls.append(words)
        return GeneratedStory(text="story", words_used=words)


class _Dictionary(DictionaryService):
    def __init__(self, entry: DictionaryEntry | None = None, raises: bool = False) -> None:
        self._entry = entry
        self._raises = raises
        self.calls: list[str] = []

    async def look_up(self, term: str) -> DictionaryEntry | None:
        self.calls.append(term)
        if self._raises:
            raise RuntimeError("upstream exploded")
        return self._entry


class _Translator:
    """Fake for both grounded modes.

    ``translations`` drives the default translate-only path as
    ``(index, native_meaning, context)``; ``suggestions`` drives the rewrite
    path. Whichever the service calls is recorded, so a test can assert not just
    the result but which mode ran.
    """

    def __init__(
        self,
        suggestions: list[MeaningSuggestion] | None = None,
        translations: list[tuple[int, str, str]] | None = None,
        raises: bool = False,
    ) -> None:
        self._suggestions = suggestions or []
        self._translations = (
            translations if translations is not None else [(0, "تضعیف کردن", "Power")]
        )
        self._raises = raises
        self.entry_texts: list[str] = []
        self.calls: list[str] = []

    async def translate_only(
        self,
        term: str,
        entry_text: str,
        learner: LearnerContext,
        max_cards: int,
    ) -> list[tuple[int, str, str]]:
        self.calls.append("translate_only")
        self.entry_texts.append(entry_text)
        if self._raises:
            raise RuntimeError("provider exploded")
        return self._translations[:max_cards]

    async def translate_senses(
        self,
        term: str,
        entry_text: str,
        learner: LearnerContext,
        max_cards: int,
    ) -> list[MeaningSuggestion]:
        self.calls.append("translate_senses")
        self.entry_texts.append(entry_text)
        if self._raises:
            raise RuntimeError("provider exploded")
        return self._suggestions[:max_cards]


def _entry(term: str = "undermine") -> DictionaryEntry:
    return DictionaryEntry(
        term=term,
        phonetic="/ʌndəˈmaɪn/",
        senses=[
            DictionarySense(
                definition="to weaken someone's authority gradually",
                part_of_speech="verb",
                example="His remarks undermined her position.",
            ),
            DictionarySense(definition="to dig beneath a structure", part_of_speech="verb"),
        ],
    )


def _card(native: str = "تضعیف کردن") -> MeaningSuggestion:
    return MeaningSuggestion(
        native_meaning=native,
        definition="to weaken someone's authority gradually",
        example="His remarks undermined her position.",
        context="Power",
        part_of_speech="verb",
    )


def _service(
    provider: _RecordingProvider,
    dictionary: DictionaryService,
    translator: _Translator,
    *,
    rewrite: bool = False,
) -> GroundedAIService:
    return GroundedAIService(
        provider,
        dictionary,
        translator,  # type: ignore[arg-type]
        max_cards=4,
        rewrite_definitions=rewrite,
    )


@pytest.mark.asyncio
async def test_dictionary_hit_translates_and_skips_generation() -> None:
    provider = _RecordingProvider()
    translator = _Translator()
    result = await _service(provider, _Dictionary(_entry()), translator).look_up_meanings(
        "undermine", LEARNER
    )

    assert result.suggestions[0].native_meaning == "تضعیف کردن"
    assert result.status is LookupStatus.OK
    assert result.notice is None
    assert translator.calls == ["translate_only"]
    # The whole point: a grounded hit must not also pay for generation.
    assert provider.lookup_calls == []


@pytest.mark.asyncio
async def test_english_comes_from_the_dictionary_not_the_model() -> None:
    """The cheap path's core guarantee: the model never authors the English."""
    translator = _Translator(translations=[(0, "تضعیف کردن", "Power")])
    result = await _service(
        _RecordingProvider(), _Dictionary(_entry()), translator
    ).look_up_meanings("undermine", LEARNER)

    card = result.suggestions[0]
    assert card.definition == "to weaken someone's authority gradually"
    assert card.example == "His remarks undermined her position."
    assert card.part_of_speech == "verb"
    # Only these two came from the model.
    assert card.native_meaning == "تضعیف کردن"
    assert card.context == "Power"


@pytest.mark.asyncio
async def test_translations_are_joined_by_index_not_by_order() -> None:
    """A headline paired with the wrong definition is the worst failure here."""
    translator = _Translator(translations=[(1, "زیر چیزی را کندن", "Structure")])
    result = await _service(
        _RecordingProvider(), _Dictionary(_entry()), translator
    ).look_up_meanings("undermine", LEARNER)

    # Index 1 is the second-ranked sense, which has no example.
    assert result.suggestions[0].definition == "to dig beneath a structure"
    assert result.suggestions[0].native_meaning == "زیر چیزی را کندن"


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [-1, 2, 99], ids=["negative", "off-by-one", "wild"])
async def test_out_of_range_indices_are_dropped_not_clamped(index: int) -> None:
    """Better no card than a Persian headline on the wrong English sense."""
    provider = _RecordingProvider()
    translator = _Translator(translations=[(index, "چیزی", "Thing")])
    result = await _service(provider, _Dictionary(_entry()), translator).look_up_meanings(
        "undermine", LEARNER
    )

    # Nothing joinable survived, so the service fell back rather than guessing.
    assert provider.lookup_calls == ["undermine"]
    assert result.suggestions[0].native_meaning == "generated"


@pytest.mark.asyncio
async def test_duplicate_indices_keep_only_the_first() -> None:
    translator = _Translator(translations=[(0, "اول", "One"), (0, "دوم", "Two")])
    result = await _service(
        _RecordingProvider(), _Dictionary(_entry()), translator
    ).look_up_meanings("undermine", LEARNER)

    assert [s.native_meaning for s in result.suggestions] == ["اول"]


@pytest.mark.asyncio
async def test_rewrite_mode_uses_the_other_prompt_and_the_models_english() -> None:
    translator = _Translator(suggestions=[_card()])
    result = await _service(
        _RecordingProvider(), _Dictionary(_entry()), translator, rewrite=True
    ).look_up_meanings("undermine", LEARNER)

    assert translator.calls == ["translate_senses"]
    assert result.suggestions[0].definition == "to weaken someone's authority gradually"


@pytest.mark.asyncio
async def test_phonetic_comes_from_the_dictionary() -> None:
    """IPA is carried, never generated — a wrong one teaches a mispronunciation."""
    result = await _service(
        _RecordingProvider(), _Dictionary(_entry()), _Translator()
    ).look_up_meanings("undermine", LEARNER)

    assert result.phonetic == "/ʌndəˈmaɪn/"


@pytest.mark.asyncio
async def test_generated_fallback_carries_no_phonetic() -> None:
    result = await _service(
        _RecordingProvider(), _Dictionary(None), _Translator()
    ).look_up_meanings("recieve", LEARNER)

    assert result.phonetic == ""


@pytest.mark.asyncio
async def test_dictionary_text_reaches_the_translator_numbered() -> None:
    """Grounding is worthless if the entry never arrives — assert it does."""
    translator = _Translator()
    await _service(_RecordingProvider(), _Dictionary(_entry()), translator).look_up_meanings(
        "undermine", LEARNER
    )

    sent = translator.entry_texts[0]
    assert "to weaken someone's authority gradually" in sent
    assert "(verb)" in sent
    # The index is the join key, so it must be visible to the model.
    assert sent.startswith("[0]")
    # Senses carrying an example rank first, so the example must survive.
    assert "His remarks undermined her position." in sent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dictionary",
    [
        _Dictionary(None),  # 404, or a rate limit, or a timeout — all None
        _Dictionary(raises=True),  # a port that breaks its own contract
    ],
    ids=["miss", "raises"],
)
async def test_falls_back_to_generation_when_dictionary_gives_nothing(
    dictionary: DictionaryService,
) -> None:
    provider = _RecordingProvider()
    result = await _service(provider, dictionary, _Translator()).look_up_meanings(
        "recieve", LEARNER
    )

    assert provider.lookup_calls == ["recieve"]
    assert result.suggestions[0].native_meaning == "generated"


@pytest.mark.asyncio
async def test_falls_back_when_translation_raises() -> None:
    provider = _RecordingProvider()
    result = await _service(
        provider, _Dictionary(_entry()), _Translator(raises=True)
    ).look_up_meanings("undermine", LEARNER)

    assert provider.lookup_calls == ["undermine"]
    assert result.suggestions[0].native_meaning == "generated"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("translations", "label"),
    [([], "none at all"), ([(0, "", "Power")], "an empty headline")],
    ids=["empty-list", "blank-native-meaning"],
)
async def test_falls_back_when_translation_returns_nothing_usable(
    translations: list[tuple[int, str, str]], label: str
) -> None:
    """A half-built card is worse than none: an empty deck must not ship."""
    provider = _RecordingProvider()
    result = await _service(
        provider, _Dictionary(_entry()), _Translator(translations=translations)
    ).look_up_meanings("undermine", LEARNER)

    assert provider.lookup_calls == ["undermine"], f"should fall back on {label}"
    assert result.suggestions[0].native_meaning == "generated"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "term",
    ["تعارف", "走る", "x" * 41, "   "],
    ids=["persian", "japanese", "too-long", "blank"],
)
async def test_ungroundable_input_never_touches_the_dictionary(term: str) -> None:
    """No English dictionary can serve these, so don't spend a request finding out."""
    dictionary = _Dictionary(_entry())
    provider = _RecordingProvider()
    await _service(provider, dictionary, _Translator()).look_up_meanings(term, LEARNER)

    assert dictionary.calls == []
    assert provider.lookup_calls == [term]


@pytest.mark.asyncio
@pytest.mark.parametrize("term", ["put off", "café"], ids=["phrasal-verb", "accented-loanword"])
async def test_plausibly_english_input_is_still_tried(term: str) -> None:
    """Anything that might be an English headword gets a speculative lookup.

    Phrasal verbs are 70% covered and accented loanwords ("café", "naïve") are
    ordinary English entries. At ~27 ms a wasted call is cheaper than the
    reasoning needed to predict a miss, so the filter only excludes input no
    English dictionary could serve.
    """
    dictionary = _Dictionary(_entry(term))
    await _service(_RecordingProvider(), dictionary, _Translator()).look_up_meanings(term, LEARNER)

    assert dictionary.calls == [term]


@pytest.mark.asyncio
async def test_suggestions_are_capped_at_max_cards() -> None:
    provider = _RecordingProvider()
    service = GroundedAIService(
        provider,
        _Dictionary(_entry()),
        _Translator(translations=[(i, f"card {i}", "Ctx") for i in range(2)]),  # type: ignore[arg-type]
        max_cards=2,
    )
    result = await service.look_up_meanings("undermine", LEARNER)

    assert len(result.suggestions) == 2


@pytest.mark.asyncio
async def test_stories_bypass_grounding_entirely() -> None:
    """A story is generated prose, not a dictionary fact — nothing to ground."""
    provider = _RecordingProvider()
    dictionary = _Dictionary(_entry())
    story = await _service(provider, dictionary, _Translator()).generate_story(
        ["run", "thrive"], LEARNER
    )

    assert story.text == "story"
    assert dictionary.calls == []


def test_entry_ranks_senses_with_examples_first() -> None:
    """Lexicographers illustrate the senses people actually use."""
    entry = DictionaryEntry(
        term="charge",
        senses=[
            DictionarySense(definition="an archaic sense", part_of_speech="noun"),
            DictionarySense(definition="a common sense", part_of_speech="noun", example="in situ"),
        ],
    )
    assert entry.top(2)[0].definition == "a common sense"
    assert entry.top(1) == [entry.senses[1]]
