"""Deck domain entity."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4


@dataclass(slots=True)
class Deck:
    id: UUID = field(default_factory=uuid4)
    user_id: UUID = field(default_factory=uuid4)
    name: str = ""
    # OKLCH hue (0..360) used by the client to colour the deck. Mirrors the design.
    hue: int = 262
    #: Identifies a logo the client ships as an asset, for decks Vocably builds
    #: itself. Deliberately a slug and not a URL: the badge must draw at full
    #: size on the first frame, offline and without shifting the card, which an
    #: image fetched over the network cannot do. A slug the client does not
    #: know falls back to the initial, so an unreleased icon degrades to what
    #: every learner-made deck already shows. Empty for those decks.
    icon: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def initial(self) -> str:
        return (self.name.strip()[:1] or "?").upper()
