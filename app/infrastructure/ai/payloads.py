"""Response payload models shared by every AI provider adapter.

Every adapter asks its provider for the same JSON shape (see ``prompts.py``), so
the same Pydantic models validate the response regardless of which provider sent
it. Providers besides ``AnthropicAIService`` reuse these directly.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.application.ports.ai_service import LookupStatus, MeaningSuggestion


class SensePayload(BaseModel):
    native_meaning: str = ""
    definition: str = ""
    example: str = ""
    context: str = ""
    part_of_speech: str = ""

    def to_dto(self) -> MeaningSuggestion:
        return MeaningSuggestion(
            native_meaning=self.native_meaning.strip(),
            definition=self.definition.strip(),
            example=self.example.strip(),
            context=self.context.strip(),
            part_of_speech=self.part_of_speech.strip(),
        )


class LookupPayload(BaseModel):
    status: LookupStatus = LookupStatus.OK
    term: str = ""
    notice: str | None = None
    #: Required, unlike its siblings, because its absence is the one reliable
    #: sign that the body is not a lookup response at all. Every real answer
    #: carries it — `unsupported` sends an explicit empty list. When it
    #: defaulted, an off-schema body (`{"word": "run", …}` from a gateway that
    #: ignores `output_config`) validated cleanly into a payload with no senses
    #: and was reported to the learner as "no suggestions for this word",
    #: hiding a transport bug behind a plausible-looking empty state.
    senses: list[SensePayload]


class StoryPayload(BaseModel):
    text: str
    words_used: list[str] = Field(default_factory=list)


class TranslationPayload(BaseModel):
    """One localised sense from the translate-only path.

    Carries no English: the definition, example and part of speech are joined
    back from the dictionary entry by ``index``. That is the point of the cheap
    path — what the model never writes, it can never corrupt.
    """

    #: Position of the sense in the numbered list the model was shown. Required:
    #: without a valid index the translation cannot be rejoined to anything, and
    #: guessing an order would silently mis-pair a headline with a definition —
    #: the worst failure this card can have.
    index: int
    native_meaning: str = ""
    context: str = ""


class TranslationsPayload(BaseModel):
    translations: list[TranslationPayload]
