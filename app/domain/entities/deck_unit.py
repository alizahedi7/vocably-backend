"""A unit/lesson inside a deck — optional grouping, never a requirement."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

#: The client renders unit names as chips; anything longer is a description.
MAX_UNIT_NAME_LENGTH = 40


@dataclass(slots=True)
class DeckUnit:
    id: UUID = field(default_factory=uuid4)
    deck_id: UUID = field(default_factory=uuid4)
    name: str = ""
    #: Order is position, not name: "Unit 10" sorts between 1 and 2
    #: alphabetically. New units take max(position) + 1.
    position: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
