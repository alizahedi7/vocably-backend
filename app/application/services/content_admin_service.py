"""Admin use cases for the content pipeline: builds and the lexicon.

Separate from :class:`AdminService` because that one is strictly read-only
analytics over aggregates, and this one mutates: retrying a build, overriding a
sense, approving content. Mixing them would blur a boundary the admin surface
documentation leans on.

Everything here is still gated per-route by ``CurrentAdmin``. Two of these
methods spend money indirectly — a retry re-queues provider calls — which is why
they are deliberate verbs rather than something a page does on load.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.application.services.deck_build_service import DeckBuildService
from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.domain.entities.deck_build import DeckBuildItem, DeckBuildJob
from app.domain.entities.lexeme import Lexeme, LexemeSense
from app.domain.entities.word import Word
from app.domain.enums import DeckBuildItemState, SenseSelection, SenseStatus
from app.domain.repositories.deck_build_repository import DeckBuildRepository
from app.domain.repositories.deck_discovery_repository import DeckDiscoveryRepository
from app.domain.repositories.lexicon_repository import LexiconRepository, LexiconStats
from app.domain.repositories.word_repository import WordRepository


@dataclass(frozen=True, slots=True)
class BuildJobDetail:
    """A job plus the things a progress screen cannot derive from it."""

    job: DeckBuildJob
    #: Item counts per state, straight from a ``GROUP BY``.
    states: dict[DeckBuildItemState, int]
    #: Rough remaining spend. An estimate, labelled as one on the screen, from a
    #: configured per-call price — never billing, and never presented as such.
    estimated_remaining_usd: float
    #: Whether the deck this job built is in Explore right now. Read from
    #: ``decks.is_public``, never from :attr:`job.state`: the build's lifecycle
    #: deliberately does not track visibility (see :class:`DeckBuildState`), so
    #: the job is not entitled to an opinion here. ``False`` for a job that has
    #: not attached a deck yet — there is nothing to publish.
    deck_is_public: bool = False
    #: When it went into Explore, or ``None`` if it is not there.
    deck_published_at: datetime | None = None

    @property
    def needs_attention(self) -> int:
        """Failed, flagged, and low-confidence items — the reviewer's whole list."""
        return self.states.get(DeckBuildItemState.FAILED, 0) + self.states.get(
            DeckBuildItemState.NEEDS_REVIEW, 0
        )


class ContentAdminService:
    def __init__(
        self,
        builds: DeckBuildRepository,
        lexicon: LexiconRepository,
        words: WordRepository,
        build_service: DeckBuildService,
        discovery: DeckDiscoveryRepository,
        *,
        current_content_version: int,
    ) -> None:
        self._builds = builds
        self._lexicon = lexicon
        self._words = words
        self._build_service = build_service
        self._discovery = discovery
        self._current_version = current_content_version

    # ── Builds ────────────────────────────────────────────────

    async def jobs(self, *, limit: int, offset: int) -> tuple[list[DeckBuildJob], int]:
        return await self._builds.list_jobs(limit=limit, offset=offset)

    async def job(self, job_id: UUID) -> BuildJobDetail:
        job = await self._builds.get_job(job_id)
        if job is None:
            raise NotFoundError("Build job not found.")
        states = await self._builds.count_by_state(job_id)
        # Every unresolved word is at most one call; words already in the lexicon
        # will cost nothing, so this is deliberately an upper bound rather than a
        # forecast — a build that comes in under it is the normal outcome.
        remaining = states.get(DeckBuildItemState.PENDING, 0) + states.get(
            DeckBuildItemState.RESOLVING, 0
        )
        # A job only has a deck once planning attached one, and a deck the admin
        # deleted out from under it is not an error here — the screen just has
        # nothing to offer publishing for.
        publication = await self._discovery.publication_of(job.deck_id) if job.deck_id else None
        return BuildJobDetail(
            job=job,
            states=states,
            estimated_remaining_usd=round(remaining * settings.ai_cost_per_call_usd, 4),
            deck_is_public=publication.is_public if publication else False,
            deck_published_at=publication.published_at if publication else None,
        )

    async def items(
        self,
        job_id: UUID,
        *,
        state: DeckBuildItemState | None = None,
        needs_attention: bool = False,
        limit: int,
        offset: int,
    ) -> tuple[list[tuple[DeckBuildItem, Word | None]], int]:
        """Items of a build, each beside the card it produced.

        Reviewing a build means judging the *card*, not the row — a reviewer
        cannot tell whether "first sense" was the right pick without reading the
        meaning, definition and example it chose. The cards are fetched in one
        query for the whole page rather than one per row.
        """
        if await self._builds.get_job(job_id) is None:
            raise NotFoundError("Build job not found.")
        items, total = await self._builds.list_items(
            job_id,
            states=(state,) if state else (),
            needs_attention=needs_attention,
            limit=limit,
            offset=offset,
        )
        cards = {
            w.id: w for w in await self._words.list_by_ids([i.word_id for i in items if i.word_id])
        }
        return [(item, cards.get(item.word_id) if item.word_id else None) for item in items], total

    async def retry(self, job_id: UUID) -> int:
        """Put a job's failed items back in the queue. Does not itself run them."""
        return await self._build_service.retry_failed(job_id)

    async def cancel(self, job_id: UUID) -> DeckBuildJob:
        return await self._build_service.cancel(job_id)

    async def override_sense(self, item_id: UUID, sense_id: UUID) -> DeckBuildItem:
        """Point one built card at a different sense of the same word.

        Records ``SenseSelection.MANUAL``, which outranks every automatic
        strategy and takes the item out of the review queue. The card itself is
        rewritten on the next run of the item — this method decides, it does not
        regenerate.
        """
        item = await self._builds.get_item(item_id)
        if item is None:
            raise NotFoundError("Build item not found.")
        sense = await self._lexicon.get_sense(sense_id)
        if sense is None:
            raise NotFoundError("Sense not found.")
        if item.lexeme_id is not None and sense.lexeme_id != item.lexeme_id:
            # Pointing a card at a sense of a different word is never a correction.
            raise ValidationError("That sense belongs to a different word.")

        item.sense_id = sense.id
        item.selection = SenseSelection.MANUAL
        item.selection_score = 1.0
        item.state = DeckBuildItemState.PENDING
        item.claimed_at = None
        item.next_attempt_at = None
        item.last_error = None
        await self._builds.save_item(item)
        return item

    # ── Lexicon ───────────────────────────────────────────────

    async def lexicon_stats(self) -> LexiconStats:
        return await self._lexicon.stats(current_version=self._current_version)

    async def lexemes(
        self,
        *,
        q: str | None,
        status: SenseStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Lexeme], int]:
        return await self._lexicon.search(q=q, status=status, limit=limit, offset=offset)

    async def lexeme(self, lexeme_id: UUID) -> Lexeme:
        lexeme = await self._lexicon.get_by_id(lexeme_id)
        if lexeme is None:
            raise NotFoundError("Word not found in the lexicon.")
        return lexeme

    async def review_queue(
        self, *, limit: int, offset: int
    ) -> tuple[list[tuple[Lexeme, LexemeSense]], int]:
        return await self._lexicon.list_senses_by_status(
            SenseStatus.NEEDS_REVIEW, limit=limit, offset=offset
        )

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
    ) -> LexemeSense:
        """The one write path that overwrites shared content.

        Allowed precisely because the caller is a human who read it first —
        every automatic path is append-only for exactly that reason. An edit does
        not propagate to cards already written; that is an explicit refresh.
        """
        updated = await self._lexicon.update_sense(
            sense_id,
            status=status,
            definition=definition,
            example=example,
            context=context,
            native_language=native_language,
            native_meaning=native_meaning,
        )
        if updated is None:
            raise NotFoundError("Sense not found.")
        return updated
