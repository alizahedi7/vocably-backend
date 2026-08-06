"""The link a teacher hands to a class.

An invite code is a **bearer credential**: holding it is what grants access, so
it is generated with a CSPRNG and carries enough entropy that guessing is not a
strategy. The client's local stand-in derives a 6-character code from
``deckId.hashCode``, which is trivially enumerable — that is a UI placeholder
and must never be copied here.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.enums import DeckRole

#: Crockford base32 without the letters that misread when a code is read aloud
#: or copied off a screen (I/L/O/U). 13 characters over this alphabet is ~65
#: bits — far past the point where guessing beats asking for the link.
_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 13


def generate_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LENGTH))


@dataclass(slots=True)
class DeckInvite:
    deck_id: UUID = field(default_factory=uuid4)
    code: str = field(default_factory=generate_code)
    #: What someone joining through the link becomes. Viewer by default: a
    #: student adding words to the teacher's deck is rarely what was meant.
    role: DeckRole = DeckRole.VIEWER
    #: False once the owner turns the link off. Members already in stay in —
    #: revoking a link is not dissolving a class.
    is_open: bool = True
    created_by_user_id: UUID | None = None
    expires_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def accepts(self, now: datetime) -> bool:
        return self.is_open and (self.expires_at is None or self.expires_at > now)
