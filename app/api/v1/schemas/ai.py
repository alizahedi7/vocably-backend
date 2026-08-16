"""AI Studio request/response schemas.

Field names are snake_case, matching the rest of the learner-facing v1 API (the
camelCase aliasing rule in CLAUDE.md applies to ``/admin/*`` only). See
``docs/ai-card-magic-contract.md`` for the design-field → API-field mapping the
"Add word" / "AI Card Magic" screens consume.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.application.dto import LookupView
from app.application.ports.ai_service import (
    GeneratedStory,
    LookupStatus,
    MeaningSuggestion,
)


class LookupIn(BaseModel):
    #: Raw learner input. May be a word, phrase, idiom, a full sentence, or a word
    #: in the learner's native language — the provider resolves it and reports how
    #: via ``LookupOut.status``.
    term: str = Field(min_length=1, max_length=200, examples=["run"])


class MeaningSuggestionOut(BaseModel):
    """One card back in the suggestion deck.

    Each field has a fixed language — see ``MeaningSuggestion``.
    """

    #: In the learner's native language. The card headline.
    native_meaning: str = ""
    #: Always English, whatever language the term is in.
    definition: str = ""
    #: One sentence, in the term's own language.
    example: str = ""
    #: Sense label chip. Always English.
    context: str = ""
    #: Always English.
    part_of_speech: str = ""

    @classmethod
    def from_dto(cls, dto: MeaningSuggestion) -> MeaningSuggestionOut:
        return cls(
            native_meaning=dto.native_meaning,
            definition=dto.definition,
            example=dto.example,
            context=dto.context,
            part_of_speech=dto.part_of_speech,
        )


class LookupOut(BaseModel):
    #: The term the suggestions describe. Differs from the submitted text when the
    #: input was corrected, extracted from a sentence, or translated.
    term: str
    suggestions: list[MeaningSuggestionOut]
    status: LookupStatus = LookupStatus.OK
    #: Short user-facing note, present whenever ``status`` is not ``ok``.
    notice: str | None = None
    #: IPA for ``term``, e.g. ``/ʌndəˈmaɪn/``. **Often empty — render it as
    #: optional.** It is supplied by the dictionary when the term is covered and
    #: is deliberately left blank rather than guessed by a model, because a
    #: confidently wrong transcription teaches a mispronunciation.
    phonetic: str = ""
    #: Stable id for *this deck of senses* — the term as resolved, at this
    #: prompt version, for this learner's language and age bucket. Send it back
    #: on ``POST /ai/feedback`` to rate one of the cards.
    #:
    #: **Empty whenever there is nothing to rate**: an ``unsupported`` result has
    #: no senses. Clients must treat an empty value as "no rating control",
    #: exactly as they treat an empty ``phonetic`` as "no transcription" — never
    #: as an error, and never as a control that posts nothing.
    #:
    #: Deterministic rather than random, which is what makes ratings from two
    #: learners who looked the same word up aggregate into one score. It names a
    #: shared dictionary entry and carries no user data.
    lookup_id: str = ""

    @classmethod
    def from_dto(cls, dto: LookupView) -> LookupOut:
        return cls(
            term=dto.result.term,
            suggestions=[MeaningSuggestionOut.from_dto(s) for s in dto.result.suggestions],
            status=dto.result.status,
            notice=dto.result.notice,
            phonetic=dto.result.phonetic,
            lookup_id=dto.lookup_id,
        )


class StoryOut(BaseModel):
    text: str
    words_used: list[str]

    @classmethod
    def from_dto(cls, dto: GeneratedStory) -> StoryOut:
        return cls(text=dto.text, words_used=dto.words_used)
