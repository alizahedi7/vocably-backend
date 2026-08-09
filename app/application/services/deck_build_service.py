"""Turning a template into a deck, one word at a time and never twice.

Two entry points, deliberately separated because they cost different things:

:meth:`DeckBuildService.plan`
    Writes the deck, its units and the whole plan. Spends nothing. Idempotent
    per template by refusing to plan a second live build of one.

:meth:`DeckBuildService.run_batch`
    Claims a batch of pending items and resolves them. This is the one that
    spends money, and every guard in this module is about spending it once.

Resolution order for a word, cheapest first::

    lexicon hit + a sense the deck can use   → 0 provider calls
    lexicon hit, sense missing, hint present → 1 enrichment call, once per item
    lexicon miss                             → the AIService chain
                                               (which may still be a cache hit)

**Transaction discipline is load-bearing.** The claim commits before any provider
call, and the result commits after. No transaction is ever held open across the
network, because under a 500-word build that is how the connection pool dies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.application.ports.ai_service import AIService, LearnerContext, LookupStatus
from app.application.ports.dictionary_service import DictionaryService
from app.application.ports.lookup_cache import normalize_lookup_input
from app.application.services.deck_access import DeckAccess
from app.application.services.lexicon_service import LexiconService
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.domain.entities.deck import Deck
from app.domain.entities.deck_build import (
    MAX_ITEM_ATTEMPTS,
    DeckBuildItem,
    DeckBuildJob,
)
from app.domain.entities.deck_template import DEFAULT_STRATEGIES, DeckTemplate
from app.domain.entities.deck_unit import DeckUnit
from app.domain.entities.lexeme import Lexeme, LexemeSense
from app.domain.entities.word import Word
from app.domain.enums import (
    DeckBuildItemState,
    DeckBuildState,
    SenseSelection,
    SenseSource,
)
from app.domain.repositories.deck_build_repository import DeckBuildRepository
from app.domain.repositories.deck_discovery_repository import DeckDiscoveryRepository
from app.domain.repositories.deck_member_repository import DeckMemberRepository
from app.domain.repositories.deck_repository import DeckRepository
from app.domain.repositories.deck_unit_repository import DeckUnitRepository
from app.domain.repositories.word_repository import WordRepository
from app.domain.services.sense_selection import SenseChoice, SenseSelector
from app.infrastructure.ai.lexicon_ai_service import SenseEnricher

logger = get_logger("vocably.deckbuild")

#: How long a claimed item may sit untouched before another worker may take it.
#: Longer than the slowest plausible provider call plus its retries, short enough
#: that a worker killed by a deploy does not park a word for the afternoon.
CLAIM_TIMEOUT_SECONDS = 600

#: How many senses one enrichment call may add. Small: it is asked for a specific
#: missing sense, and a model offered room for four will find four.
MAX_ENRICHED_SENSES = 2


@dataclass(frozen=True, slots=True)
class BatchOutcome:
    """What one ``run_batch`` did, and whether the driver should come back."""

    processed: int = 0
    done: int = 0
    failed: int = 0
    retrying: int = 0
    has_more: bool = False
    #: Set when the job reached a terminal state during this batch.
    finished_state: DeckBuildState | None = None

    def summary(self) -> str:
        return (
            f"processed={self.processed} done={self.done} "
            f"failed={self.failed} retrying={self.retrying}"
        )


class DeckBuildService:
    def __init__(
        self,
        builds: DeckBuildRepository,
        decks: DeckRepository,
        members: DeckMemberRepository,
        units: DeckUnitRepository,
        words: WordRepository,
        discovery: DeckDiscoveryRepository,
        lexicon: LexiconService,
        ai: AIService,
        *,
        content_version: int,
        enricher: SenseEnricher | None = None,
        dictionary: DictionaryService | None = None,
        claim_timeout_seconds: int = CLAIM_TIMEOUT_SECONDS,
    ) -> None:
        self._builds = builds
        self._decks = decks
        self._members = members
        self._units = units
        self._words = words
        self._discovery = discovery
        self._lexicon = lexicon
        self._ai = ai
        self._content_version = content_version
        self._enricher = enricher
        #: Pronunciation only. A card's IPA is **carried, never generated** — no
        #: model is ever asked for one, because a confidently wrong transcription
        #: teaches a learner to mispronounce a word. ``None`` disables the lookup
        #: entirely and every card is simply written without one.
        self._dictionary = dictionary
        self._claim_timeout = claim_timeout_seconds

    # ── Planning ──────────────────────────────────────────────

    async def plan(
        self,
        template: DeckTemplate,
        *,
        owner_id: UUID,
    ) -> DeckBuildJob:
        """Create the deck, its units and the item plan. Spends nothing.

        The deck is created **private** and stays private: nothing in this
        pipeline ever sets ``is_public``. Publishing is the admin's deliberate
        act through the endpoint that already exists, which is what makes a
        half-built deck invisible by construction rather than by vigilance.
        """
        existing = await self._builds.active_job_for(template.slug)
        if existing is not None:
            raise ValidationError(
                f"A build of {template.slug!r} is already {existing.state.value}. "
                "Cancel it before planning another."
            )
        if not template.units:
            raise ValidationError("The template has no units to build.")

        deck = await self._decks.add(Deck(user_id=owner_id, name=template.name, hue=template.hue))
        await self._members.add(DeckAccess.owner(deck.id, owner_id))
        # Metadata only — is_public stays false and published_at stays null.
        await self._discovery.set_published(
            deck.id,
            is_public=False,
            is_official=template.official,
            category=template.category,
            description=template.description,
            description_fa=template.description_fa,
            published_at=None,
        )

        unit_ids = await self._create_units(deck.id, template)
        job = DeckBuildJob(
            template_slug=template.slug,
            template_version=template.version,
            template_hash=template.content_hash,
            deck_id=deck.id,
            state=DeckBuildState.PLANNED,
            # Pinned here, for the whole run: a deploy that bumps the prompt at
            # word 300 of 504 must not write the second half under a different
            # version than the first.
            content_version=self._content_version,
            native_language=template.generation.native_language,
            register=template.generation.register,
            category=template.category,
            strategies=template.generation.strategies,
            created_by_user_id=owner_id,
        )
        items = [
            DeckBuildItem(
                job_id=job.id,
                position=position,
                unit_label=unit.name,
                unit_position=unit_index,
                source_term=word.term,
                normalized=normalize_lookup_input(word.term),
                hint=word.hint,
            )
            for position, (unit_index, unit, word) in enumerate(template.words_in_order())
        ]
        created = await self._builds.create_job(job, items)
        logger.info("planned build %s: %d words in %d units", created.id, len(items), len(unit_ids))
        return created

    async def _create_units(self, deck_id: UUID, template: DeckTemplate) -> dict[int, UUID]:
        """One unit per lesson, in template order. Positions come from the file.

        Ordered by ``position`` and never by name, because "Lesson 10" sorts
        between 1 and 2 alphabetically.
        """
        unit_ids: dict[int, UUID] = {}
        for index, unit in enumerate(template.units):
            created = await self._units.add(
                DeckUnit(deck_id=deck_id, name=unit.name, position=index)
            )
            unit_ids[index] = created.id
        return unit_ids

    # ── Building ──────────────────────────────────────────────

    async def run_batch(self, job_id: UUID, *, limit: int) -> BatchOutcome:
        """Claim up to ``limit`` items, resolve each, and report.

        Safe to call concurrently and safe to call on a finished job: the claim
        is conditional, so a second worker or a redelivered message simply finds
        nothing to take.
        """
        job = await self._builds.get_job(job_id)
        if job is None:
            raise NotFoundError("Build job not found.")
        if job.state.is_terminal:
            return BatchOutcome(finished_state=job.state)
        if job.state is DeckBuildState.PLANNED:
            await self._builds.set_job_state(
                job.id, DeckBuildState.GENERATING, started_at=datetime.now(UTC)
            )

        now = datetime.now(UTC)
        items = await self._builds.claim_batch(
            job.id,
            limit=limit,
            now=now,
            reclaim_before=now - timedelta(seconds=self._claim_timeout),
        )
        if not items:
            return await self._finalise(job)

        unit_ids = await self._unit_map(job)
        learner = LearnerContext(native_language=job.native_language)
        selector = self._selector_for(job)

        processed = done = failed = retrying = 0
        for item in items:
            processed += 1
            try:
                await self._resolve(job, item, learner, selector, unit_ids)
                done += 1
            except Exception as exc:  # noqa: BLE001 — one word must not stop the job
                outcome = await self._record_failure(job, item, exc)
                if outcome is DeckBuildItemState.FAILED:
                    failed += 1
                else:
                    retrying += 1

        has_more = await self._builds.has_open_items(job.id)
        if not has_more:
            finished = await self._finalise(job)
            return BatchOutcome(
                processed=processed,
                done=done,
                failed=failed,
                retrying=retrying,
                has_more=False,
                finished_state=finished.finished_state,
            )
        return BatchOutcome(
            processed=processed, done=done, failed=failed, retrying=retrying, has_more=True
        )

    async def _resolve(
        self,
        job: DeckBuildJob,
        item: DeckBuildItem,
        learner: LearnerContext,
        selector: SenseSelector,
        unit_ids: dict[int, UUID],
    ) -> None:
        lexeme = await self._lexicon.find(item.source_term)
        reused = lexeme is not None and bool(lexeme.servable_senses(job.register))

        if not reused:
            lexeme = await self._generate(job, item, learner)
        if lexeme is None:
            raise ValidationError(f"No usable senses could be produced for {item.source_term!r}")

        await self._fill_phonetic(lexeme)

        senses = lexeme.servable_senses(job.register)
        choice = selector.select(senses, item.hint)

        # Enrich when the deck asked for a particular sense and did not get it.
        # Not only when *nothing* was selectable: with `first` in the chain a
        # fallback always answers, so waiting for `None` would mean a pinned
        # sense that is genuinely missing silently became the wrong card.
        if self._wants_enrichment(item, choice):
            lexeme = await self._enrich(job, item, lexeme, learner)
            senses = lexeme.servable_senses(job.register)
            choice = selector.select(senses, item.hint)

        if choice is None:
            # Enrichment is spent, or was not allowed, and nothing scored. Write
            # the card from the first sense and say so — an unreviewed card a
            # human can fix beats a hole in Lesson 12.
            if not senses:
                raise ValidationError(f"{item.source_term!r} has no servable senses")
            item.selection = SenseSelection.FIRST
            item.selection_score = None
            item.state = DeckBuildItemState.NEEDS_REVIEW
            chosen = senses[0]
        else:
            item.selection = choice.strategy
            item.selection_score = choice.score
            item.state = (
                DeckBuildItemState.NEEDS_REVIEW if choice.needs_review else DeckBuildItemState.DONE
            )
            chosen = choice.sense

        word = await self._materialise(job, item, lexeme, chosen, unit_ids)

        item.lexeme_id = lexeme.id
        item.sense_id = chosen.id
        item.word_id = word.id
        item.last_error = None
        item.claimed_at = None
        await self._builds.save_item(item)
        await self._builds.bump_counters(
            job.id,
            items_done=1,
            lexemes_reused=1 if reused else 0,
            lexemes_generated=0 if reused else 1,
        )

    async def _generate(
        self, job: DeckBuildJob, item: DeckBuildItem, learner: LearnerContext
    ) -> Lexeme | None:
        """Buy the word. Goes through the full ``AIService`` chain, never around it.

        That is what makes "a word a learner looked up last month costs the deck
        builder nothing" structural: the cache and the lexicon are layers of that
        chain, so a hit anywhere in it is a hit here.
        """
        result = await self._ai.look_up_meanings(item.source_term, learner)
        await self._builds.bump_counters(job.id, ai_calls=1)
        if result.status is LookupStatus.UNSUPPORTED or not result.suggestions:
            raise ValidationError(
                f"The provider returned no senses for {item.source_term!r} "
                f"(status={result.status.value})"
            )
        # The chain's lexicon layer has normally already written this; recording
        # again is a no-op thanks to the unique keys, and covers the case where
        # the lexicon layer is disabled but a build is running anyway.
        await self._lexicon.record(
            result,
            learner,
            source=SenseSource.DECK_BUILD,
            register=job.register,
        )
        return await self._lexicon.find(item.source_term)

    async def _enrich(
        self,
        job: DeckBuildJob,
        item: DeckBuildItem,
        lexeme: Lexeme,
        learner: LearnerContext,
    ) -> Lexeme:
        """Ask for the one sense this deck needs and the lexicon lacks.

        Marked spent on the item **before** the call, so a failure mid-enrichment
        cannot turn into a second one on the retry. One paid attempt per word per
        deck, whatever happens to it.
        """
        item.enriched = True
        if self._enricher is None or not lexeme.has_room(job.register):
            return lexeme

        wanted = item.hint.gloss or _pin_description(item)
        known = [
            suggestion
            for sense in lexeme.servable_senses(job.register)
            if (suggestion := sense.to_suggestion(job.native_language)) is not None
        ]
        new_senses = await self._enricher.enrich_senses(
            lexeme.display_term or item.source_term,
            known,
            wanted,
            learner,
            MAX_ENRICHED_SENSES,
        )
        await self._builds.bump_counters(job.id, ai_calls=1)
        if not new_senses:
            # A legitimate answer: the word may simply not have that sense.
            logger.info("enrichment returned nothing for %r", item.source_term)
            return lexeme

        updated = await self._lexicon.append_senses(
            lexeme, new_senses, learner, register=job.register
        )
        await self._builds.bump_counters(job.id, senses_enriched=1)
        return updated

    async def _fill_phonetic(self, lexeme: Lexeme) -> None:
        """Give the headword an IPA transcription if it has none yet.

        Free and fast — a Wiktionary mirror at ~27 ms, Redis-cached across every
        learner and every deck — so it is worth doing inline rather than leaving
        to the nightly backfill, which would publish the deck without
        pronunciation and fill it in days later.

        Three rules carried over from ``words.phonetic`` unchanged, because the
        distinction they encode is what stops this being re-asked forever:

        * ``None`` means *not looked up yet*; ``""`` means *the dictionary
          answered and this word has no IPA* — roughly a third of them. Both
          render as nothing.
        * A **miss and an outage look identical** through the port, so neither is
          recorded as ``""``. Such words stay ``None`` and the nightly backfill
          tries again; writing ``""`` here would let one bad afternoon silence a
          set of cards permanently.
        * Never load-bearing: any failure leaves the card without an IPA, which
          is exactly what a hand-written card looks like.
        """
        if self._dictionary is None or lexeme.phonetic is not None:
            return
        try:
            entry = await self._dictionary.look_up(lexeme.display_term or lexeme.lemma)
        except Exception as exc:  # noqa: BLE001 — the port promises not to raise
            logger.info("phonetic lookup raised: %s", type(exc).__name__)
            return
        if entry is None:
            # A miss or an outage — indistinguishable here, so record neither.
            return
        await self._lexicon.set_phonetic(lexeme.id, entry.phonetic)
        lexeme.phonetic = entry.phonetic

    async def _materialise(
        self,
        job: DeckBuildJob,
        item: DeckBuildItem,
        lexeme: Lexeme,
        sense: LexemeSense,
        unit_ids: dict[int, UUID],
    ) -> Word:
        """Write the card — a **copy** of the sense, not a view of it.

        A copy because ``words`` is what the whole application reads, because a
        learner may edit their own copy, and because ``CLAUDE.md`` forbids that
        edit ever flowing back into shared content. ``lexeme_sense_id`` keeps the
        one thing a copy loses: the handle an explicit refresh needs.

        Rewriting rather than inserting on a retry, so an item resolved twice
        leaves one card rather than two.
        """
        if job.deck_id is None:  # pragma: no cover — planning always attaches one
            raise ValidationError(f"Build job {job.id} has no deck to write cards into.")
        native = sense.translation_for(job.native_language)
        word = Word(
            deck_id=job.deck_id,
            created_by_user_id=job.created_by_user_id,
            unit_id=unit_ids.get(item.unit_position),
            term=lexeme.display_term or item.source_term,
            meaning=native.native_meaning if native else "",
            definition=sense.definition or None,
            example=sense.example or None,
            sense_label=sense.context or None,
            phonetic=lexeme.phonetic,
            lexeme_sense_id=sense.id,
        )
        if item.word_id is not None:
            existing = await self._words.get(item.word_id)
            if existing is not None:
                word.id = existing.id
                word.created_at = existing.created_at
                return await self._words.update(word)
        return await self._words.add(word)

    # ── Failure, retry, completion ────────────────────────────

    async def _record_failure(
        self, job: DeckBuildJob, item: DeckBuildItem, exc: Exception
    ) -> DeckBuildItemState:
        item.attempts += 1
        item.last_error = f"{type(exc).__name__}: {exc}"
        item.claimed_at = None
        if item.is_exhausted:
            item.state = DeckBuildItemState.FAILED
            item.next_attempt_at = None
            await self._builds.save_item(item)
            await self._builds.bump_counters(job.id, items_failed=1)
            logger.warning(
                "build %s gave up on %r after %d attempts: %s",
                job.id,
                item.source_term,
                item.attempts,
                item.last_error,
            )
            return DeckBuildItemState.FAILED

        item.state = DeckBuildItemState.PENDING
        item.next_attempt_at = datetime.now(UTC) + timedelta(seconds=item.backoff_seconds())
        await self._builds.save_item(item)
        logger.info(
            "build %s will retry %r in %ds (attempt %d/%d)",
            job.id,
            item.source_term,
            item.backoff_seconds(),
            item.attempts,
            MAX_ITEM_ATTEMPTS,
        )
        return DeckBuildItemState.PENDING

    async def _finalise(self, job: DeckBuildJob) -> BatchOutcome:
        """Settle the job once no item is open.

        ``PARTIAL`` rather than ``FAILED`` when items failed: 501 good cards and
        three holes is a deck worth reviewing, and ``reset_failed`` can retry the
        three without rebuilding anything.
        """
        counts = await self._builds.count_by_state(job.id)
        if counts.get(DeckBuildItemState.PENDING) or counts.get(DeckBuildItemState.RESOLVING):
            return BatchOutcome(has_more=True)

        failed = counts.get(DeckBuildItemState.FAILED, 0)
        state = DeckBuildState.PARTIAL if failed else DeckBuildState.COMPLETED
        await self._builds.set_job_state(job.id, state, finished_at=datetime.now(UTC))
        logger.info("build %s finished as %s (%d failed)", job.id, state.value, failed)
        return BatchOutcome(has_more=False, finished_state=state)

    async def cancel(self, job_id: UUID) -> DeckBuildJob:
        job = await self._builds.get_job(job_id)
        if job is None:
            raise NotFoundError("Build job not found.")
        if job.state.is_terminal:
            return job
        await self._builds.set_job_state(
            job_id, DeckBuildState.CANCELLED, finished_at=datetime.now(UTC)
        )
        # Items are left exactly as they are. Cancelling stops new work; it does
        # not undo cards already written, and resuming is a matter of retrying.
        refreshed = await self._builds.get_job(job_id)
        return refreshed or job

    async def retry_failed(self, job_id: UUID) -> int:
        """Put failed items back in the queue and re-open the job."""
        job = await self._builds.get_job(job_id)
        if job is None:
            raise NotFoundError("Build job not found.")
        reset = await self._builds.reset_failed(job_id)
        if reset:
            await self._builds.set_job_state(job_id, DeckBuildState.GENERATING, finished_at=None)
        return reset

    # ── Helpers ───────────────────────────────────────────────

    def _wants_enrichment(self, item: DeckBuildItem, choice: SenseChoice | None) -> bool:
        """Whether this word is worth one paid call for a sense it may not have.

        Three conditions, all necessary. The item must not have spent its one
        attempt; there must be an enricher; and the template must actually have
        asked for something — with no hint at all, "the selector fell back" means
        the deck did not care which sense it got, not that one is missing.
        """
        if item.enriched or self._enricher is None or item.hint.is_empty:
            return False
        # A confident strategy found what was asked for. Nothing to buy.
        return choice is None or not choice.strategy.is_confident

    def _selector_for(self, job: DeckBuildJob) -> SenseSelector:
        """Built from the job row, never from the template file.

        The plan is already in the database, so a build must survive the template
        being edited or moved underneath it — and a run that silently changed
        strategy halfway through would produce a deck nobody could review.
        """
        return SenseSelector(
            strategies=job.strategies or DEFAULT_STRATEGIES,
            category=job.category,
        )

    async def _unit_map(self, job: DeckBuildJob) -> dict[int, UUID]:
        """Position → unit id, read back rather than remembered.

        Read fresh every batch because a build spans processes: the planner's
        in-memory map does not survive to the worker that resolves word 400.
        """
        if job.deck_id is None:
            return {}
        units = await self._units.list_for_deck(job.deck_id)
        return {unit.position: unit.id for unit in units}


def _pin_description(item: DeckBuildItem) -> str:
    """What to ask an enricher for when the template pinned rather than described."""
    if item.hint.is_pinned:
        return f"the {item.hint.part_of_speech} sense labelled {item.hint.context!r}"
    return item.hint.gloss
