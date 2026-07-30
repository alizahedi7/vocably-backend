"""Roll the ``word_reviews`` partition window forward, and prune expired months.

``word_reviews`` is partitioned by month (see the ``e4b7d91a5c02`` migration).
Partitions do not appear on their own, so this must run on a schedule — a daily
cron or Kubernetes CronJob is the intended home::

    make partitions              # create the upcoming months; never deletes
    make partitions prune=1      # also drop months past the retention window

Creation is idempotent and safe to run as often as you like. Pruning is
**destructive and opt-in**: dropping a partition discards every review in that
month, permanently, so it is never implied by a plain run.

The window is ``REVIEW_HISTORY_PARTITION_LOOKAHEAD_MONTHS`` wide and retention is
``REVIEW_HISTORY_RETENTION_MONTHS`` (``0`` keeps everything).

Exits non-zero if the DEFAULT partition holds rows. That partition exists only so
inserts cannot fail when maintenance has lapsed; rows landing in it mean this job
has not run in a very long time. It also has a real cost — while it is non-empty,
creating a partition whose range overlaps those rows must scan and revalidate it
under an exclusive lock, so an unnoticed default is how a routine maintenance run
turns into an outage.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import date

from sqlalchemy import text

from app.core.config import settings
from app.core.database import async_session_factory, engine
from app.core.logging import configure_logging, get_logger

logger = get_logger("vocably.partitions")

#: Matches the names ``ensure_word_reviews_partition`` generates, and so
#: excludes ``word_reviews_default``, which must never be dropped.
_PARTITION_NAME = re.compile(r"^word_reviews_(\d{4})(\d{2})$")


def _add_months(start: date, count: int) -> date:
    """``start`` shifted by ``count`` whole months, normalised to the 1st."""
    total = (start.year * 12 + start.month - 1) + count
    return date(total // 12, total % 12 + 1, 1)


def _partition_month(name: str) -> date | None:
    match = _PARTITION_NAME.match(name)
    if match is None:
        return None
    return date(int(match.group(1)), int(match.group(2)), 1)


async def maintain(*, prune: bool) -> int:
    configure_logging()

    if engine.dialect.name != "postgresql":
        logger.error(
            "Partitioning is a Postgres feature; this database is %s.", engine.dialect.name
        )
        return 1

    this_month = date.today().replace(day=1)
    exit_code = 0

    async with async_session_factory() as session:
        # ── create the window ahead ──────────────────────────
        lookahead = max(1, settings.review_history_partition_lookahead_months)
        created = []
        for offset in range(lookahead + 1):  # inclusive: this month plus the window
            month = _add_months(this_month, offset)
            name = (
                await session.execute(
                    text("SELECT ensure_word_reviews_partition(:month)"), {"month": month}
                )
            ).scalar_one()
            created.append(name)
        await session.commit()
        logger.info("Partition window covers %s through %s.", created[0], created[-1])

        # ── prune expired months ─────────────────────────────
        existing = list(
            (
                await session.execute(
                    text(
                        "SELECT c.relname FROM pg_class c "
                        "JOIN pg_inherits i ON i.inhrelid = c.oid "
                        "WHERE i.inhparent = 'word_reviews'::regclass ORDER BY c.relname"
                    )
                )
            )
            .scalars()
            .all()
        )

        retention = settings.review_history_retention_months
        if retention <= 0:
            logger.info("Retention is disabled; keeping all %d partitions.", len(existing))
        else:
            cutoff = _add_months(this_month, -retention)
            dated = ((name, _partition_month(name)) for name in existing)
            # A None month is word_reviews_default (or a hand-made partition) —
            # never a pruning candidate.
            expired = sorted(name for name, month in dated if month is not None and month < cutoff)
            if not expired:
                logger.info("No partitions older than %s to prune.", cutoff)
            elif not prune:
                # Loud, but does not act: deleting a learner's history is not
                # something a routine maintenance run should do by implication.
                logger.warning(
                    "%d partition(s) are past the %d-month retention and awaiting "
                    "pruning (%s). Re-run with --prune to drop them.",
                    len(expired),
                    retention,
                    ", ".join(expired),
                )
            else:
                for name in expired:
                    # Identifier is built from a \d{6} regex match, never user input.
                    await session.execute(text(f'DROP TABLE IF EXISTS "{name}"'))
                    logger.info("Dropped expired partition %s.", name)
                await session.commit()

        # ── the default partition must stay empty ────────────
        stray = (
            await session.execute(text("SELECT count(*) FROM ONLY word_reviews_default"))
        ).scalar_one()
        if stray:
            logger.error(
                "word_reviews_default holds %d row(s) — partition maintenance has not run "
                "in far too long. Create the missing months and move these rows out; while "
                "the default is non-empty, creating an overlapping partition locks and "
                "rescans it.",
                stray,
            )
            exit_code = 1

    await engine.dispose()
    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create upcoming word_reviews partitions and prune expired ones."
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Drop partitions past the retention window. Destructive and irreversible.",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(maintain(prune=args.prune)))


if __name__ == "__main__":
    main()
