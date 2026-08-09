"""Validate, plan and run a pre-built deck build from the command line.

The trigger lives here rather than in the admin panel because the templates live
in git: a deck's word list arrives as a pull request, and the person who merges
it is the person who should start the build. The admin panel owns what it is
genuinely better at — watching progress, working the review queue, and
publishing — and its "start build" button, when it lands, enqueues the identical
Celery task so the two cannot drift.

Three verbs, in the order they cost money::

    make deck-validate slug=504-essential-words   # offline, free
    make deck-plan     slug=504-essential-words   # writes rows, spends nothing
    make deck-build    job=<id>                   # spends

``deck-build`` runs batches in-process by default, so a build works on a machine
with no Celery worker at all. ``queue=1`` hands it to the worker instead, which
is what production does.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.deck_build_service import DeckBuildService
from app.application.services.lexicon_service import LexiconService
from app.core.config import settings
from app.core.database import async_session_factory
from app.core.logging import configure_logging, get_logger
from app.domain.entities.deck_template import TemplateError
from app.domain.enums import AuthMethod, DeckBuildState
from app.infrastructure.ai.factory import (
    configured_model,
    effective_prompt_version,
    lookup_chain,
    raw_ai_provider,
)
from app.infrastructure.ai.lexicon_ai_service import SenseEnricher
from app.infrastructure.db.models.user import UserModel
from app.infrastructure.db.repositories.deck_build_repository import (
    SqlAlchemyDeckBuildRepository,
)
from app.infrastructure.db.repositories.deck_discovery_repository import (
    SqlAlchemyDeckDiscoveryRepository,
)
from app.infrastructure.db.repositories.deck_member_repository import (
    SqlAlchemyDeckMemberRepository,
)
from app.infrastructure.db.repositories.deck_repository import SqlAlchemyDeckRepository
from app.infrastructure.db.repositories.deck_unit_repository import SqlAlchemyDeckUnitRepository
from app.infrastructure.db.repositories.lexicon_repository import SqlAlchemyLexiconRepository
from app.infrastructure.db.repositories.word_repository import SqlAlchemyWordRepository
from app.infrastructure.templates.deck_template_loader import (
    list_templates,
    load_template,
    validate_template,
)

logger = get_logger("vocably.build_deck")


# ── validate ─────────────────────────────────────────────────


def validate(slug: str) -> int:
    """Parse and check a template. Touches no database and no provider."""
    try:
        template = load_template(slug)
    except TemplateError as exc:
        print(f"✗ {slug}: {exc}", file=sys.stderr)
        return 1

    report = validate_template(template)
    print(f"✓ {template.name} ({slug} v{template.version})")
    print(f"  {template.unit_count} units · {template.word_count} words")
    print(
        f"  {report.pinned} pinned sense(s) · {report.hinted} hinted · category {template.category}"
    )
    print(f"  strategies: {' → '.join(s.value for s in template.generation.strategies)}")
    if template.source.title:
        print(f"  source: {template.source.title}")
        print(f"  rights: {template.source.rights.strip() or '(none stated)'}")
    for warning in report.warnings:
        print(f"  ! {warning}")
    for term, places in report.duplicates[:10]:
        print(f"    · {term!r} appears in {', '.join(places)}")
    print(f"  hash: {template.content_hash[:16]}…")
    return 0


# ── plan ─────────────────────────────────────────────────────


async def plan(slug: str, owner: str | None) -> int:
    configure_logging()
    try:
        template = load_template(slug)
    except TemplateError as exc:
        print(f"✗ {slug}: {exc}", file=sys.stderr)
        return 1

    async with async_session_factory() as session:
        owner_id = await _resolve_owner(session, owner)
        service = _service(session)
        try:
            job = await service.plan(template, owner_id=owner_id)
        except Exception as exc:  # noqa: BLE001 — a CLI reports, it does not traceback
            await session.rollback()
            print(f"✗ {exc}", file=sys.stderr)
            return 1
        await session.commit()

    print(f"✓ planned {template.name}")
    print(f"  job  {job.id}")
    print(f"  deck {job.deck_id}  (private — publishing stays a separate, deliberate act)")
    print(f"  {job.items_total} items · content version {job.content_version}")
    print(f"\n  next: make deck-build job={job.id}")
    return 0


async def _resolve_owner(session: AsyncSession, owner: str | None) -> UUID:
    """The account official decks belong to, created on first use.

    A real ``users`` row because decks, memberships and cards all key off one —
    but never a person. It has no auth method that can sign in, and account
    deletion must never be pointed at it.
    """
    identifier = owner or settings.content_owner_phone
    stmt = select(UserModel).where(UserModel.phone == identifier)
    existing = (await session.execute(stmt)).scalars().first()
    if existing is not None:
        return existing.id

    user = UserModel(
        auth_method=AuthMethod.PHONE.value,
        phone=identifier,
        name="Vocably",
        onboarded=True,
    )
    session.add(user)
    await session.flush()
    logger.info("created content owner account %s", identifier)
    return user.id


# ── build ────────────────────────────────────────────────────


async def build(job_id: UUID, *, queue: bool, max_batches: int) -> int:
    configure_logging()
    if queue:
        from app.tasks.deck_build import build_deck

        build_deck.delay(str(job_id))
        print(f"✓ queued build {job_id} on the AI queue")
        return 0

    batches = 0
    while batches < max_batches:
        async with async_session_factory() as session:
            service = _service(session)
            outcome = await service.run_batch(job_id, limit=settings.deck_build_batch_size)
            await session.commit()

        batches += 1
        print(f"  batch {batches}: {outcome.summary()}")
        if not outcome.has_more:
            state = outcome.finished_state.value if outcome.finished_state else "idle"
            print(f"✓ build {job_id} is {state}")
            return 0 if state != DeckBuildState.FAILED.value else 1
        if outcome.processed == 0:
            # Work remains but none of it is claimable yet — every open item is
            # sitting out its retry backoff. Looping here would spin the CPU
            # against the database for minutes; the Celery driver paces itself
            # with a countdown, and a person at a terminal should just be told.
            print(f"… {job_id} has work waiting on retry backoff. Run this again in a few minutes.")
            return 0

    # Not an error: retries have backoff, so a build with a few failing words
    # legitimately needs another invocation once their delays elapse.
    print(f"… stopped after {batches} batches; run again to continue")
    return 0


def _service(session: AsyncSession) -> DeckBuildService:
    lexicon = LexiconService(
        SqlAlchemyLexiconRepository(session),
        content_version=effective_prompt_version(),
        provider=settings.ai_provider,
        model=configured_model(),
    )
    return DeckBuildService(
        SqlAlchemyDeckBuildRepository(session),
        SqlAlchemyDeckRepository(session),
        SqlAlchemyDeckMemberRepository(session),
        SqlAlchemyDeckUnitRepository(session),
        SqlAlchemyWordRepository(session),
        SqlAlchemyDeckDiscoveryRepository(session),
        lexicon,
        lookup_chain(session, lexicon),
        content_version=effective_prompt_version(),
        enricher=cast("SenseEnricher", raw_ai_provider()),
        claim_timeout_seconds=settings.deck_build_claim_timeout_seconds,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build pre-built decks from templates.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="check a template offline")
    p_validate.add_argument("slug", nargs="?", help="template slug; omit to list them")

    p_plan = sub.add_parser("plan", help="create the deck, units and item plan")
    p_plan.add_argument("slug")
    p_plan.add_argument("--owner", default=None, help="phone of the account to own the deck")

    p_build = sub.add_parser("build", help="resolve items for a planned job")
    p_build.add_argument("job_id")
    p_build.add_argument("--queue", action="store_true", help="hand to a Celery worker instead")
    p_build.add_argument("--max-batches", type=int, default=1000)

    args = parser.parse_args()

    if args.command == "validate":
        if not args.slug:
            for slug in list_templates():
                print(slug)
            return 0
        return validate(args.slug)
    if args.command == "plan":
        return asyncio.run(plan(args.slug, args.owner))
    return asyncio.run(build(UUID(args.job_id), queue=args.queue, max_batches=args.max_batches))


if __name__ == "__main__":
    raise SystemExit(main())
