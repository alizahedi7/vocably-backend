"""Fill in the IPA on cards that were saved without one.

Most cards get their transcription for free: the learner looked the word up,
the dictionary answered, and the client posted the ``phonetic`` back with the
card. This service is for the rest — a card typed by hand, a deck saved from
Explore before its source had one, and every row that predates
``words.phonetic`` existing at all.

Two properties make it cheap enough to run as ordinary background work:

* **Keyed by term, not by card.** One dictionary call answers for every learner
  who ever typed that word, so a thousand cards of "run" cost one request.
* **It mostly converges.** ``NULL`` means *no answer yet* and ``""`` means *the
  dictionary answered and this word has no IPA* — about a third of words.
  Without that distinction the job would re-fetch the same permanent misses on
  every run, forever, and never shrink its own backlog.

The one thing it will not do is write ``""`` because a lookup *failed*. The
dictionary port collapses "no such word" and "we are rate limited" into the same
``None``, and treating that as "this word has no pronunciation" would make an
afternoon's outage permanently silence a set of cards. Those rows stay ``NULL``
and come round again — which is why the term ordering is random rather than
oldest-first.

Nothing here is load-bearing. A dictionary that is down, rate-limited or simply
has no entry leaves the card exactly as it was: no phonetic, and a card front
that renders without one.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.application.ports.dictionary_service import DictionaryService
from app.core.logging import get_logger
from app.domain.repositories.word_repository import WordRepository

logger = get_logger("vocably.phonetics")

#: Longest transcription ``words.phonetic`` can hold. A dictionary answer over
#: this is not a transcription — it is some other field's content leaking into
#: one — so it is dropped rather than truncated into nonsense.
MAX_PHONETIC_CHARS = 200


@dataclass(frozen=True, slots=True)
class BackfillResult:
    """What one run did, for the task's log line and its tests."""

    #: Terms looked up in the dictionary this run.
    terms_checked: int
    #: Of those, how many came back with a transcription.
    terms_found: int
    #: Terms the dictionary could not answer for — a miss or an outage,
    #: indistinguishable here. Left unwritten, so they come round again.
    terms_unanswered: int
    #: Card rows written, including the ones marked as having no transcription.
    words_updated: int

    @property
    def exhausted(self) -> bool:
        """True when no card was left unchecked — the backlog is clear."""
        return self.terms_checked == 0


class PhoneticBackfillService:
    def __init__(self, words: WordRepository, dictionary: DictionaryService) -> None:
        self._words = words
        self._dictionary = dictionary

    async def run(self, *, limit: int) -> BackfillResult:
        """Look up the next ``limit`` unchecked terms and record the answers.

        Safe to run twice: each write only touches rows still holding ``NULL``,
        so a redelivered task re-asks at worst a handful of terms and writes
        nothing over an answer already stored.
        """
        terms = await self._words.list_terms_missing_phonetic(limit)
        found = 0
        unanswered = 0
        updated = 0
        for term in terms:
            phonetic = await self._look_up(term)
            if phonetic is None:
                unanswered += 1
                continue
            if phonetic:
                found += 1
            # The empty answer is written too, and that is the point: it is what
            # takes a covered-but-untranscribed word out of the next run.
            updated += await self._words.set_phonetic_for_term(term, phonetic)
        logger.info(
            "phonetic backfill: checked=%d found=%d unanswered=%d rows=%d",
            len(terms),
            found,
            unanswered,
            updated,
        )
        return BackfillResult(
            terms_checked=len(terms),
            terms_found=found,
            terms_unanswered=unanswered,
            words_updated=updated,
        )

    async def _look_up(self, term: str) -> str | None:
        """The dictionary's IPA for ``term``.

        Three outcomes, and the difference between the last two is the whole
        design: a transcription; ``""`` for a word the dictionary covers but has
        no IPA for; and ``None`` for no answer at all, which must **not** be
        recorded as "this word has no pronunciation" — it is equally the shape
        of an outage.
        """
        try:
            entry = await self._dictionary.look_up(term)
        except Exception as exc:  # noqa: BLE001 — a backfill must not crash a worker
            logger.warning("phonetic lookup failed for %r: %s", term, type(exc).__name__)
            return None
        if entry is None:
            return None
        phonetic = entry.phonetic.strip()
        if len(phonetic) > MAX_PHONETIC_CHARS:
            # Not a transcription — some other field has leaked into it. The
            # word itself was answered for, so it is recorded as having none
            # rather than left to be asked again with the same result.
            logger.warning("discarding %d-char phonetic for %r", len(phonetic), term)
            return ""
        return phonetic
