"""AI Studio request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.application.ports.ai_service import GeneratedStory, MeaningSuggestion


class LookupIn(BaseModel):
    term: str = Field(min_length=1, max_length=255, examples=["run"])


class MeaningSuggestionOut(BaseModel):
    meaning: str
    context: str
    example: str

    @classmethod
    def from_dto(cls, dto: MeaningSuggestion) -> MeaningSuggestionOut:
        return cls(meaning=dto.meaning, context=dto.context, example=dto.example)


class LookupOut(BaseModel):
    term: str
    suggestions: list[MeaningSuggestionOut]


class StoryOut(BaseModel):
    text: str
    words_used: list[str]

    @classmethod
    def from_dto(cls, dto: GeneratedStory) -> StoryOut:
        return cls(text=dto.text, words_used=dto.words_used)
