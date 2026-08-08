"""Word (flashcard) ORM model — the card, shared by everyone in its deck."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.infrastructure.db.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class WordModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "words"

    #: Attribution only — never an authorization check. Who may read or edit
    #: this card is deck membership (``deck_members``), and nothing else.
    #:
    #: ``SET NULL``, and nullable, because attribution is precisely what may be
    #: lost when someone leaves: a word an editor added to a teacher's deck
    #: belongs to the deck, so their account going away must leave the class's
    #: vocabulary standing, merely uncredited. RESTRICT was tried and made
    #: every account undeletable — see revision e2c6a94f5b70.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    deck_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("decks.id", ondelete="CASCADE"), index=True, nullable=False
    )

    #: The unit this card sits in, or NULL. ``ondelete="SET NULL"`` *is* the
    #: product rule: deleting a unit keeps its cards and drops them back into
    #: the deck. Removing a heading must never remove what was under it.
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("deck_units.id", ondelete="SET NULL"), index=True
    )

    term: Mapped[str] = mapped_column(String(255), nullable=False)
    meaning: Mapped[str] = mapped_column(Text, nullable=False)
    definition: Mapped[str | None] = mapped_column(Text)
    example: Mapped[str | None] = mapped_column(Text)
    sense_label: Mapped[str | None] = mapped_column(String(120))

    #: IPA for ``term``. Nullable and usually null: the dictionary covers most
    #: but not all words, and a card typed by hand has none until the backfill
    #: task fills it in. Bounded rather than ``Text`` because it is one short
    #: transcription of one card front, never prose.
    phonetic: Mapped[str | None] = mapped_column(String(200))
