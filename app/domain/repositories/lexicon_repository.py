"""Port: persistence for the shared lexicon.

Two properties every implementation must honour, both inherited from the lookup
cache's contract and both load-bearing here for stronger reasons:

* **Impersonal.** No ``user_id`` is accepted, stored or returned. A learner's
  edits to their own card go to ``words`` and never come back here — one
  person's wording must not be served to everybody.
* **Concurrent-write safe.** A user lookup and a deck build reaching the same
  brand-new word at the same moment is the normal case, not an edge case. Writes
  insert with ``ON CONFLICT DO NOTHING``, so the loser of that race costs one
  duplicated provider call and never a duplicated sense or a 500.

``DO NOTHING`` rather than ``DO UPDATE`` throughout, and deliberately: the second
writer must not overwrite what the first wrote, because the first may have been a
human whose approval the second would silently discard.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from uuid import UUID

from app.domain.entities.lexeme import Lexeme, LexemeSense
from app.domain.enums import SenseStatus


@dataclass(frozen=True, slots=True)
class LexiconStats:
    """Whole-lexicon counters for the admin dashboard."""

    lexemes: int = 0
    senses: int = 0
    translations: int = 0
    needs_review: int = 0
    rejected: int = 0
    approved: int = 0
    #: Senses written by a pipeline version older than the current one. Reported
    #: only — regeneration is always an explicit, bounded command.
    stale: int = 0


class LexiconRepository(ABC):
    @abstractmethod
    async def get(self, lemma: str, *, language: str = "en") -> Lexeme | None:
        """The lexeme for a normalized lemma, with every sense and translation.

        ``lemma`` must already be normalized by ``normalize_lookup_input``: the
        column stores the normalized form, and normalising in two places is how
        two spellings of one word end up as two lexemes.
        """

    @abstractmethod
    async def get_by_id(self, lexeme_id: UUID) -> Lexeme | None: ...

    @abstractmethod
    async def get_sense(self, sense_id: UUID) -> LexemeSense | None: ...

    @abstractmethod
    async def upsert(
        self,
        lemma: str,
        *,
        language: str = "en",
        display_term: str,
        phonetic: str | None = None,
    ) -> Lexeme:
        """Insert the headword if absent, and return it either way.

        Never overwrites ``display_term``. ``phonetic`` is filled only when the
        stored value is NULL — ``""`` is a real answer ("this word has no IPA")
        and must not be replaced by a later lookup that also found none.
        """

    @abstractmethod
    async def add_senses(self, lexeme_id: UUID, senses: list[LexemeSense]) -> list[LexemeSense]:
        """Append senses and their translations, skipping ones already present.

        Returns the senses **as stored**, which for an existing ``sense_key`` is
        the row that was already there rather than the one passed in. Callers use
        that to tell a real append from a no-op without a second query.

        Existing senses are never renumbered: a published deck may point at
        position 2, and reordering would silently repoint it.
        """

    @abstractmethod
    async def add_translation_if_absent(
        self,
        sense_id: UUID,
        *,
        native_language: str,
        native_meaning: str,
        content_version: int,
    ) -> bool:
        """Give an existing sense a headline in a language it lacks one in.

        The point of splitting translations off senses: a learner whose native
        language is new to the platform re-buys short headlines, not the English
        half of the corpus. ``False`` when a headline already exists — never an
        overwrite, because the one there may be a human's.
        """

    @abstractmethod
    async def set_phonetic(self, lexeme_id: UUID, phonetic: str) -> None:
        """Record an IPA answer, including the empty one.

        Only writes over NULL. See ``Lexeme.phonetic`` for why ``""`` and NULL
        are different and why the difference has to survive.
        """

    @abstractmethod
    async def update_sense(
        self,
        sense_id: UUID,
        *,
        status: SenseStatus | None = None,
        definition: str | None = None,
        example: str | None = None,
        context: str | None = None,
        native_language: str | None = None,
        native_meaning: str | None = None,
    ) -> LexemeSense | None:
        """Apply an admin's edit. Omitted fields are left alone.

        The one write path that may overwrite existing content, because the one
        caller is a human who read it first.
        """

    @abstractmethod
    async def search(
        self,
        *,
        q: str | None = None,
        status: SenseStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Lexeme], int]:
        """Browse the lexicon. ``status`` filters to lexemes holding such a sense."""

    @abstractmethod
    async def list_senses_by_status(
        self,
        status: SenseStatus,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[tuple[Lexeme, LexemeSense]], int]:
        """The review queue: senses in one status, with their headword."""

    @abstractmethod
    async def stats(self, *, current_version: int) -> LexiconStats:
        """Counters for the dashboard, computed in SQL rather than in Python."""
