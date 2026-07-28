"""Response payload models shared by every AI provider adapter.

Every adapter asks its provider for the same JSON shape (see ``prompts.py``), so
the same Pydantic models validate the response regardless of which provider sent
it. Providers besides ``AnthropicAIService`` reuse these directly.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.application.ports.ai_service import LookupStatus, MeaningSuggestion


class SensePayload(BaseModel):
    context: str = ""
    part_of_speech: str = ""
    native_meaning: str = ""
    meaning: str = ""
    definition: str = ""
    examples: list[str] = Field(default_factory=list)
    synonyms: list[str] = Field(default_factory=list)
    antonyms: list[str] = Field(default_factory=list)
    collocations: list[str] = Field(default_factory=list)

    def to_dto(self) -> MeaningSuggestion:
        examples = [e.strip() for e in self.examples if e.strip()]
        return MeaningSuggestion(
            # `meaning` and `example` are the original narrow contract; keep them
            # populated even when the model leans on the richer fields, so older
            # clients never render a blank card.
            meaning=self.meaning.strip() or self.definition.strip(),
            context=self.context.strip(),
            example=examples[0] if examples else "",
            part_of_speech=self.part_of_speech.strip(),
            native_meaning=self.native_meaning.strip(),
            definition=self.definition.strip(),
            examples=tuple(examples),
            synonyms=tuple(s.strip() for s in self.synonyms if s.strip()),
            antonyms=tuple(a.strip() for a in self.antonyms if a.strip()),
            collocations=tuple(c.strip() for c in self.collocations if c.strip()),
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
