"""Codec between :class:`MeaningSuggestion` and the cache's stored JSON.

The adapters already refuse to trust the shape of what a provider returns
(``payloads.py``). What comes back out of the cache deserves exactly the same
suspicion: it was written by an older deploy, against an older field list, and a
partially-filled card is worse than a cache miss. So the stored blob is
re-validated with Pydantic on the way out, and anything that fails is reported as
a miss.

``SCHEMA_VERSION`` covers *storage* shape only — the fields on the card. The
*content* of a card is versioned separately by
:data:`~app.infrastructure.ai.prompts.PROMPT_VERSION`, which is part of the cache
key. Adding an optional field to the card back is a schema bump; changing what
the model writes into it is a prompt bump.
"""

from __future__ import annotations

from typing import Any, Final

from pydantic import BaseModel, ValidationError

from app.application.ports.ai_service import MeaningSuggestion

#: Bump when the stored shape changes incompatibly. Rows at another version are
#: read as misses and swept later, so a bump never needs a data migration.
SCHEMA_VERSION: Final = 2


class _StoredSense(BaseModel):
    native_meaning: str = ""
    definition: str = ""
    example: str = ""
    context: str = ""
    part_of_speech: str = ""

    def to_dto(self) -> MeaningSuggestion:
        return MeaningSuggestion(
            native_meaning=self.native_meaning,
            definition=self.definition,
            example=self.example,
            context=self.context,
            part_of_speech=self.part_of_speech,
        )


class _StoredPayload(BaseModel):
    v: int
    senses: list[_StoredSense]


def encode(suggestions: list[MeaningSuggestion]) -> dict[str, Any]:
    """Serialise senses for storage."""
    return {
        "v": SCHEMA_VERSION,
        "senses": [
            {
                "native_meaning": s.native_meaning,
                "definition": s.definition,
                "example": s.example,
                "context": s.context,
                "part_of_speech": s.part_of_speech,
            }
            for s in suggestions
        ],
    }


def decode(raw: object) -> list[MeaningSuggestion] | None:
    """Deserialise a stored payload, or ``None`` if it is not usable as written."""
    try:
        payload = _StoredPayload.model_validate(raw)
    except ValidationError:
        return None
    if payload.v != SCHEMA_VERSION:
        return None
    return [s.to_dto() for s in payload.senses]
