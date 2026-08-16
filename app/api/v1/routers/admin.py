"""Admin dashboard endpoints — read-only, platform-wide analytics.

Every route is gated by :data:`CurrentAdmin` (see ``require_admin``), so only
users with ``is_admin`` set may reach them. Responses use camelCase aliases to
match the ``vocably-admin`` dashboard's TypeScript types.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import (
    AdminServiceDep,
    ContentAdminServiceDep,
    CurrentAdmin,
    DeckDiscoveryServiceDep,
    FeedbackServiceDep,
)
from app.api.v1.schemas.admin import (
    AdminAIFeedbackPageOut,
    AdminAISenseScoreOut,
    AdminCacheAliasOut,
    AdminCacheAliasPageOut,
    AdminCacheEntryOut,
    AdminCacheEntryPageOut,
    AdminCacheOverviewOut,
    AdminCategoryOut,
    AdminFeedbackPageOut,
    AdminFeedbackReportOut,
    AdminPublishDeckIn,
    AdminUserOut,
    AdminWordOut,
    AuthMethodBreakdownOut,
    BuildItemOut,
    BuildItemPageOut,
    BuildItemSenseIn,
    BuildJobDetailOut,
    BuildJobOut,
    BuildJobPageOut,
    LexemeOut,
    LexemePageOut,
    LexiconStatsOut,
    OverviewOut,
    RetryOut,
    ReviewQueuePageOut,
    ReviewQueueRowOut,
    SenseOut,
    SenseUpdateIn,
    TimeSeriesPointOut,
)
from app.domain.enums import DeckBuildItemState, SenseStatus
from app.infrastructure.ai.factory import effective_prompt_version

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/overview", response_model=OverviewOut)
async def overview(_admin: CurrentAdmin, admin: AdminServiceDep) -> OverviewOut:
    return OverviewOut.from_dto(await admin.overview())


@router.get("/registrations", response_model=list[TimeSeriesPointOut])
async def registrations(
    _admin: CurrentAdmin,
    admin: AdminServiceDep,
    days: int = Query(default=30, ge=1, le=365),
) -> list[TimeSeriesPointOut]:
    return [TimeSeriesPointOut.from_dto(point) for point in await admin.registrations(days)]


@router.get("/auth-methods", response_model=list[AuthMethodBreakdownOut])
async def auth_methods(
    _admin: CurrentAdmin, admin: AdminServiceDep
) -> list[AuthMethodBreakdownOut]:
    return [AuthMethodBreakdownOut.from_dto(row) for row in await admin.auth_methods()]


@router.get("/users", response_model=list[AdminUserOut])
async def users(_admin: CurrentAdmin, admin: AdminServiceDep) -> list[AdminUserOut]:
    return [AdminUserOut.from_dto(row) for row in await admin.users()]


@router.get("/categories", response_model=list[AdminCategoryOut])
async def categories(_admin: CurrentAdmin, admin: AdminServiceDep) -> list[AdminCategoryOut]:
    return [AdminCategoryOut.from_dto(row) for row in await admin.categories()]


@router.get("/words", response_model=list[AdminWordOut])
async def words(_admin: CurrentAdmin, admin: AdminServiceDep) -> list[AdminWordOut]:
    return [AdminWordOut.from_dto(row) for row in await admin.words()]


@router.get("/cache/overview", response_model=AdminCacheOverviewOut)
async def cache_overview(_admin: CurrentAdmin, admin: AdminServiceDep) -> AdminCacheOverviewOut:
    return AdminCacheOverviewOut.from_dto(await admin.cache_overview())


@router.get("/cache/entries/{entry_id}", response_model=AdminCacheEntryOut)
async def cache_entry(
    entry_id: UUID, _admin: CurrentAdmin, admin: AdminServiceDep
) -> AdminCacheEntryOut:
    return AdminCacheEntryOut.from_dto(await admin.cache_entry(entry_id))


@router.get("/cache/entries", response_model=AdminCacheEntryPageOut)
async def cache_entries(
    _admin: CurrentAdmin,
    admin: AdminServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None, max_length=255),
) -> AdminCacheEntryPageOut:
    rows, total = await admin.cache_entries(limit, offset, q)
    items = [AdminCacheEntryOut.from_dto(row) for row in rows]
    return AdminCacheEntryPageOut(items=items, total=total)


@router.get("/cache/entries/{entry_id}/aliases", response_model=AdminCacheAliasPageOut)
async def cache_entry_aliases(
    entry_id: UUID,
    _admin: CurrentAdmin,
    admin: AdminServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AdminCacheAliasPageOut:
    rows, total = await admin.cache_aliases(entry_id, limit, offset)
    return AdminCacheAliasPageOut(
        items=[AdminCacheAliasOut.from_dto(row) for row in rows], total=total
    )


@router.patch("/decks/{deck_id}/publish", status_code=status.HTTP_204_NO_CONTENT)
async def publish_deck(
    deck_id: UUID,
    payload: AdminPublishDeckIn,
    _admin: CurrentAdmin,
    discovery: DeckDiscoveryServiceDep,
) -> None:
    """Put a deck in Explore, or take it out.

    Deliberately the only mutating route on the admin surface, and deliberately
    not available to deck owners: Explore has no report path and no moderation
    queue yet, so publishing is a curated act. Opening it to owners later is a
    permission change, not a migration — see CLAUDE.md.
    """
    await discovery.set_published(
        deck_id,
        is_public=payload.is_public,
        is_official=payload.is_official,
        category=payload.category,
        description=payload.description,
        description_fa=payload.description_fa,
    )


# ── Content pipeline ─────────────────────────────────────────
# Read-only except for four verbs: retry, cancel, override a card's sense, and
# edit or approve a lexicon sense. All four are deliberate acts by a human who
# has looked at what they are changing — which is why the automatic paths are
# append-only and this one is not.


@router.get("/builds", response_model=BuildJobPageOut)
async def builds(
    _admin: CurrentAdmin,
    content: ContentAdminServiceDep,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> BuildJobPageOut:
    jobs, total = await content.jobs(limit=limit, offset=offset)
    return BuildJobPageOut(items=[BuildJobOut.from_entity(j) for j in jobs], total=total)


@router.get("/builds/{job_id}", response_model=BuildJobDetailOut)
async def build_detail(
    job_id: UUID, _admin: CurrentAdmin, content: ContentAdminServiceDep
) -> BuildJobDetailOut:
    return BuildJobDetailOut.from_dto(await content.job(job_id))


@router.get("/builds/{job_id}/items", response_model=BuildItemPageOut)
async def build_items(
    job_id: UUID,
    _admin: CurrentAdmin,
    content: ContentAdminServiceDep,
    # Annotated form for these two only: ruff's B008 exemption for FastAPI
    # defaults does not cover enum-typed parameters, and Annotated is the
    # style FastAPI documents anyway.
    state: Annotated[DeckBuildItemState | None, Query()] = None,
    needs_attention: bool = Query(default=False, alias="needsAttention"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> BuildItemPageOut:
    """Items of a build.

    ``needsAttention=true`` is the review queue: what failed, what was flagged,
    and what a guessing strategy chose. On a good build that is a few dozen rows
    out of five hundred, which is what makes review something that happens.
    """
    rows, total = await content.items(
        job_id,
        state=state,
        needs_attention=needs_attention,
        limit=limit,
        offset=offset,
    )
    return BuildItemPageOut(
        items=[BuildItemOut.from_entity(item, card) for item, card in rows], total=total
    )


@router.post("/builds/{job_id}/retry", response_model=RetryOut)
async def retry_build(
    job_id: UUID, _admin: CurrentAdmin, content: ContentAdminServiceDep
) -> RetryOut:
    """Return a build's failed items to the queue, attempts cleared.

    Does not itself resolve them — a worker (or ``make deck-build``) picks them
    up. Retrying costs money, so it stays an explicit act rather than something
    a progress page does on its own.
    """
    return RetryOut(requeued=await content.retry(job_id))


@router.post("/builds/{job_id}/cancel", response_model=BuildJobOut)
async def cancel_build(
    job_id: UUID, _admin: CurrentAdmin, content: ContentAdminServiceDep
) -> BuildJobOut:
    """Stop taking new work. Cards already written stay written."""
    return BuildJobOut.from_entity(await content.cancel(job_id))


@router.patch("/build-items/{item_id}/sense", response_model=BuildItemOut)
async def override_item_sense(
    item_id: UUID,
    payload: BuildItemSenseIn,
    _admin: CurrentAdmin,
    content: ContentAdminServiceDep,
) -> BuildItemOut:
    """Point one card at a different sense of the same word.

    Records the choice as ``manual``, which outranks every automatic strategy and
    takes the item out of the review queue. The card is rewritten the next time
    the item runs.
    """
    item = await content.override_sense(item_id, payload.sense_id)
    return BuildItemOut.from_entity(item)


@router.get("/lexicon/stats", response_model=LexiconStatsOut)
async def lexicon_stats(_admin: CurrentAdmin, content: ContentAdminServiceDep) -> LexiconStatsOut:
    return LexiconStatsOut.from_dto(
        await content.lexicon_stats(), current_version=effective_prompt_version()
    )


@router.get("/lexicon", response_model=LexemePageOut)
async def lexicon(
    _admin: CurrentAdmin,
    content: ContentAdminServiceDep,
    q: str | None = Query(default=None, max_length=255),
    status_filter: Annotated[SenseStatus | None, Query(alias="status")] = None,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> LexemePageOut:
    lexemes, total = await content.lexemes(q=q, status=status_filter, limit=limit, offset=offset)
    return LexemePageOut(items=[LexemeOut.from_entity(x) for x in lexemes], total=total)


@router.get("/lexicon/review", response_model=ReviewQueuePageOut)
async def lexicon_review_queue(
    _admin: CurrentAdmin,
    content: ContentAdminServiceDep,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ReviewQueuePageOut:
    """Senses that passed schema validation but tripped a soft rule.

    Oldest first: the queue is meant to be worked down, not sampled.
    """
    rows, total = await content.review_queue(limit=limit, offset=offset)
    return ReviewQueuePageOut(
        items=[
            ReviewQueueRowOut(
                lexeme_id=lexeme.id,
                term=lexeme.display_term or lexeme.lemma,
                sense=SenseOut.from_entity(sense),
            )
            for lexeme, sense in rows
        ],
        total=total,
    )


@router.get("/lexicon/{lexeme_id}", response_model=LexemeOut)
async def lexeme(
    lexeme_id: UUID, _admin: CurrentAdmin, content: ContentAdminServiceDep
) -> LexemeOut:
    return LexemeOut.from_entity(await content.lexeme(lexeme_id))


@router.patch("/lexicon/senses/{sense_id}", response_model=SenseOut)
async def update_sense(
    sense_id: UUID,
    payload: SenseUpdateIn,
    _admin: CurrentAdmin,
    content: ContentAdminServiceDep,
) -> SenseOut:
    """Edit or approve one shared sense.

    The only write path in the system that overwrites shared content, and it is
    allowed precisely because a human read it first. An edit does not propagate
    to cards already written — those hold their own copy, deliberately, and
    pushing a correction into them is a separate explicit act.
    """
    sense = await content.update_sense(
        sense_id,
        status=payload.status,
        definition=payload.definition,
        example=payload.example,
        context=payload.context,
        native_language=payload.native_language,
        native_meaning=payload.native_meaning,
    )
    return SenseOut.from_entity(sense)


# ── feedback ─────────────────────────────────────────────────
# Reached through ``FeedbackServiceDep`` rather than ``AdminServiceDep``, which
# is a deliberate departure from the layering note in CLAUDE.md. ``AdminService``
# exists to aggregate over *other features'* tables; feedback has its own table
# and its own service, and routing its reads through a second repository would
# smear one vertical slice across two. What is not departed from is the rule that
# matters: both routes take ``CurrentAdmin``, or they would be open to anyone
# holding a token.


@router.get("/feedback", response_model=AdminFeedbackPageOut)
async def feedback_reports(
    _admin: CurrentAdmin,
    feedback: FeedbackServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    kind: str | None = Query(default=None, max_length=16),
) -> AdminFeedbackPageOut:
    """What learners have written in, newest first.

    ``kind`` narrows to ``bug`` | ``idea`` | ``other``; anything unrecognised
    reads as ``other``, which is where an unrecognised kind was stored too.
    """
    reports, total = await feedback.list_reports(limit=limit, offset=offset, kind=kind)
    return AdminFeedbackPageOut(
        items=[AdminFeedbackReportOut.from_entity(r) for r in reports], total=total
    )


@router.get("/ai-feedback", response_model=AdminAIFeedbackPageOut)
async def ai_feedback(
    _admin: CurrentAdmin,
    feedback: FeedbackServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AdminAIFeedbackPageOut:
    """How the AI card backs are doing: the overall split, then the worst first.

    Rows are per *sense*, not per lookup. A word whose second card is wrong and
    whose first is fine is a fixable prompt problem, and averaging the two hides
    exactly the thing worth seeing. Join ``lookupId`` against
    ``/admin/cache/entries`` to read the text that was rated.
    """
    scores, totals = await feedback.ai_scores(limit=limit, offset=offset)
    return AdminAIFeedbackPageOut(
        items=[AdminAISenseScoreOut.from_dto(s) for s in scores],
        ups=totals.ups,
        downs=totals.downs,
        total=totals.rated_senses,
    )
