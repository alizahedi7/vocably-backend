"""Seed the database with a demo user, decks, and words.

Run with ``make seed`` (or ``python -m app.scripts.seed``). Idempotent: it no-ops if the
demo user already exists.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.logging import configure_logging, get_logger
from app.domain.enums import AgeRange, AuthMethod, DeckRole, LeitnerBox
from app.domain.services.leitner import interval_for
from app.infrastructure.db.models.deck import DeckModel
from app.infrastructure.db.models.deck_member import DeckMemberModel
from app.infrastructure.db.models.user import UserModel
from app.infrastructure.db.models.word import WordModel
from app.infrastructure.db.models.word_progress import WordProgressModel

logger = get_logger("vocably.seed")

DEMO_PHONE = "5551234567"

_DECKS = [
    ("Everyday English", 262),
    ("Work & Business", 55),
    ("Travel English", 190),
]

# (deck_index, term, meaning, sense_label, example, box)
_WORDS = [
    (
        0,
        "reliable",
        "able to be trusted",
        "adjective · character",
        "She is a reliable friend who always keeps her word.",
        3,
    ),
    (
        0,
        "improve",
        "to make or become better",
        "verb · progress",
        "I want to improve my English every day.",
        1,
    ),
    (
        1,
        "schedule",
        "a plan of times for doing things",
        "noun · work",
        "My schedule is completely full this week.",
        2,
    ),
    (
        0,
        "curious",
        "eager to know or learn something",
        "adjective · character",
        "Children are naturally curious about the world.",
        4,
    ),
    (
        0,
        "borrow",
        "to take and use something temporarily",
        "verb · everyday",
        "Can I borrow your pen for a minute?",
        1,
    ),
    (
        1,
        "achieve",
        "to succeed in reaching a goal",
        "verb · progress",
        "She achieved her dream of studying abroad.",
        5,
    ),
    (
        2,
        "luggage",
        "bags and suitcases for traveling",
        "noun · travel",
        "My luggage was too heavy at the airport.",
        2,
    ),
    (
        0,
        "neighborhood",
        "the area around where you live",
        "noun · places",
        "Our neighborhood is quiet and green.",
        3,
    ),
    (
        1,
        "deadline",
        "the latest time to finish something",
        "noun · work",
        "The deadline for the report is Friday.",
        1,
    ),
    (
        2,
        "departure",
        "the act of leaving a place",
        "noun · travel",
        "Our departure time is six in the morning.",
        5,
    ),
    (
        2,
        "itinerary",
        "a planned route or journey",
        "noun · travel",
        "The itinerary includes three days in Rome.",
        4,
    ),
]


async def seed() -> None:
    configure_logging()
    now = datetime.now(UTC)

    async with async_session_factory() as session:
        existing = (
            await session.execute(select(UserModel).where(UserModel.phone == DEMO_PHONE))
        ).scalar_one_or_none()
        if existing is not None:
            logger.info("Demo user already exists (%s) — nothing to do.", existing.id)
            return

        user = UserModel(
            auth_method=AuthMethod.PHONE.value,
            phone=DEMO_PHONE,
            name="Parisa",
            age_range=AgeRange.ADULT_25.value,
            native_language="Persian",
            app_language="English",
            streak=7,
            # Dated, or the demo account's streak would be a number with no day
            # behind it: settling on read leaves it alone (there is nothing to
            # measure from), but the first day it banked would restart it at 1.
            streak_last_day=now.date(),
            streak_banked_on=now.date(),
            last_studied_on=now.date(),
            onboarded=True,
        )
        session.add(user)
        await session.flush()

        decks: list[DeckModel] = []
        for name, hue in _DECKS:
            deck = DeckModel(user_id=user.id, name=name, hue=hue)
            session.add(deck)
            decks.append(deck)
        await session.flush()

        # Membership, not decks.user_id, is what the app reads: a deck with no
        # deck_members row is invisible to everyone, including its creator.
        for deck in decks:
            session.add(
                DeckMemberModel(
                    deck_id=deck.id,
                    user_id=user.id,
                    role=DeckRole.OWNER.value,
                    joined_at=now,
                )
            )

        for deck_idx, term, meaning, sense, example, box in _WORDS:
            leitner_box = LeitnerBox(box)
            # Lower boxes are due now; higher boxes are scheduled into the future.
            due_at = now - timedelta(days=1) if box <= 3 else now + interval_for(leitner_box)
            word = WordModel(
                created_by_user_id=user.id,
                deck_id=decks[deck_idx].id,
                term=term,
                meaning=meaning,
                sense_label=sense,
                example=example,
            )
            session.add(word)
            await session.flush()
            # The seed deliberately writes progress rows: it exists to show a
            # populated account, and boxes only exist per learner now.
            session.add(
                WordProgressModel(
                    user_id=user.id,
                    word_id=word.id,
                    deck_id=word.deck_id,
                    box=box,
                    due_at=due_at,
                )
            )

        await session.commit()
        logger.info(
            "Seeded demo user %s with %d decks and %d words.",
            user.id,
            len(_DECKS),
            len(_WORDS),
        )


if __name__ == "__main__":
    asyncio.run(seed())
