"""Word (flashcard) domain entity — the card's content, shared by a whole deck.

One learner's boxes and due dates used to live here too, which made a deck
one person's by construction. They now live in
:class:`~app.domain.entities.word_progress.WordProgress`, keyed
``(user_id, word_id)``, so a class can share one deck and hold thirty separate
sets of progress against it. See
:class:`~app.domain.entities.studied_word.StudiedWord` for the pair as one
learner sees it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4


@dataclass(slots=True)
class Word:
    id: UUID = field(default_factory=uuid4)
    deck_id: UUID = field(default_factory=uuid4)
    #: Who added the card. **Attribution only** — never an authorization check.
    #: Who may read or edit a card is deck membership, and nothing else.
    created_by_user_id: UUID | None = None
    #: The unit/lesson this card belongs to within its deck, or ``None``. Most
    #: decks have no units at all, and a card belonging to none is normal.
    unit_id: UUID | None = None

    # Content
    term: str = ""
    meaning: str = ""
    # Plain-language dictionary definition of the chosen sense — the "DEFINITION"
    # body of the card back. Filled in by AI Card Magic
    # (``MeaningSuggestion.definition``) or written by the learner.
    definition: str | None = None
    example: str | None = None
    # e.g. "verb · progress" or "my definition" — mirrors the design's senseLabel.
    sense_label: str | None = None
    # IPA transcription of ``term``, e.g. ``/ʌndəˈmaɪn/``. **Carried, never
    # generated**: it comes from the dictionary via ``LookupResult.phonetic``,
    # and is left empty rather than guessed, because a confidently wrong
    # transcription teaches a learner to mispronounce a word — worse than
    # showing nothing. ``None`` is the normal case (a third of words have no
    # IPA, and every hand-written card starts without one), so every surface
    # renders it as optional and shows nothing at all when it is absent.
    phonetic: str | None = None

    #: The shared lexicon sense this card was built from, when it was built by
    #: the deck pipeline rather than typed. Provenance only: the card carries its
    #: own copy of the text, so an explicit refresh — never an implicit one — is
    #: what pushes a corrected sense back into cards nobody has edited.
    #: ``None`` for every hand-written card, which is most of them.
    lexeme_sense_id: UUID | None = None

    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
