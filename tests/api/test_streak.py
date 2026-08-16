"""The daily streak, end to end.

One rule: **the streak advances when the day's goal is met, at most once per
local day.** The goal is met by reviewing — either ``daily_goal`` cards, or the
day's queue was handed out and is now empty.

What each test here guards is a way the old rule was wrong: one card banked the
day, and nothing ever settled a streak on a day the learner did not study, so a
lost streak went on being drawn as alive until the next review quietly reset it
to 1.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.models.user import UserModel
from tests.api.conftest import sleep_on_it


async def seed(
    client: AsyncClient, headers: dict[str, str], terms: list[str]
) -> tuple[str, list[str]]:
    deck_id: str = (
        await client.post("/api/v1/decks", headers=headers, json={"name": "Deck", "hue": 262})
    ).json()["id"]
    word_ids: list[str] = []
    for term in terms:
        created = await client.post(
            "/api/v1/words",
            headers=headers,
            json={"deck_id": deck_id, "term": term, "meaning": f"meaning of {term}"},
        )
        assert created.status_code == 201, created.text
        word_ids.append(created.json()["id"])
    return deck_id, word_ids


async def grade(client: AsyncClient, headers: dict[str, str], word_id: str) -> None:
    response = await client.post(
        f"/api/v1/study/words/{word_id}/grade", headers=headers, json={"grade": "good"}
    )
    assert response.status_code == 200, response.text


async def overview(client: AsyncClient, headers: dict[str, str]) -> dict:
    response = await client.get("/api/v1/study/overview", headers=headers)
    assert response.status_code == 200, response.text
    body: dict = response.json()
    return body


async def set_goal(client: AsyncClient, headers: dict[str, str], goal: int) -> None:
    response = await client.patch("/api/v1/users/me", headers=headers, json={"daily_goal": goal})
    assert response.status_code == 200, response.text


async def backdate_streak(
    session_factory: async_sessionmaker[AsyncSession], *, days: int, streak: int
) -> date:
    """Put the learner's last banked day ``days`` ago, with ``streak`` behind it."""
    when = date.today() - timedelta(days=days)
    async with session_factory() as session:
        user = (await session.execute(select(UserModel))).scalars().first()
        assert user is not None
        user.streak = streak
        user.streak_last_day = when
        user.streak_banked_on = when
        await session.commit()
    return when


# ── meeting the goal ──


async def test_the_goal_banks_the_day_and_only_the_first_time(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await set_goal(client, auth_headers, 5)
    _, word_ids = await seed(client, auth_headers, [f"w{i}" for i in range(6)])
    await sleep_on_it(session_factory)

    for answered, word_id in enumerate(word_ids[:4], start=1):
        await grade(client, auth_headers, word_id)
        body = await overview(client, auth_headers)
        assert body["streak"] == 0, f"banked after only {answered} of 5"
        assert body["day_state"] == "open"

    await grade(client, auth_headers, word_ids[4])
    body = await overview(client, auth_headers)
    assert body["streak"] == 1
    assert body["day_state"] == "banked"

    # The sixth review is still work, and still earns nothing further: the day
    # is locked once it is banked.
    await grade(client, auth_headers, word_ids[5])
    body = await overview(client, auth_headers)
    assert body["streak"] == 1
    assert body["day_state"] == "banked"


async def test_clearing_a_short_queue_banks_a_day_under_the_goal(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The light-day path: four due, a goal of ten, and they finished.

    Making somebody over-review to hold a streak is how a streak stops meaning
    anything.
    """
    await set_goal(client, auth_headers, 10)
    _, word_ids = await seed(client, auth_headers, ["a", "b", "c", "d"])
    await sleep_on_it(session_factory)

    for word_id in word_ids[:3]:
        await grade(client, auth_headers, word_id)
    assert (await overview(client, auth_headers))["day_state"] == "open"

    await grade(client, auth_headers, word_ids[3])
    body = await overview(client, auth_headers)
    assert body["due_count"] == 0
    assert body["streak"] == 1
    assert body["day_state"] == "banked"


async def test_a_brand_new_deck_cannot_bank_the_day(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """The hole the ``due_reviews`` counter closes.

    A card added today is tomorrow's work, so a new deck's queue is empty from
    the moment it is created. Without counting *scheduled* answers separately,
    "the queue is empty" would be true here and one review would bank the day —
    which is the behaviour being replaced.
    """
    await set_goal(client, auth_headers, 10)
    _, word_ids = await seed(client, auth_headers, ["fresh", "new"])

    for word_id in word_ids:
        await grade(client, auth_headers, word_id)

    body = await overview(client, auth_headers)
    assert body["due_count"] == 0
    assert body["streak"] == 0
    assert body["day_state"] == "rest"


async def test_a_day_with_no_reviews_at_all_banks_nothing(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await set_goal(client, auth_headers, 5)
    await seed(client, auth_headers, ["a", "b"])
    await sleep_on_it(session_factory)

    body = await overview(client, auth_headers)
    assert body["streak"] == 0
    assert body["day_state"] == "open"


# ── settling on read ──


async def test_a_missed_day_lapses_when_the_home_screen_is_opened(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The defect this whole change is built on.

    The streak moved only when a card was graded, so two days away left the old
    number on screen — correct-looking, stale, and wrong — until the next
    review silently reset it to 1. The learner was never told.
    """
    await seed(client, auth_headers, ["a", "b"])
    await sleep_on_it(session_factory)
    await backdate_streak(session_factory, days=3, streak=7)

    body = await overview(client, auth_headers)
    assert body["streak"] == 0
    assert body["day_state"] == "open"

    # And it is settled, not merely reported: the next read agrees without
    # having to work it out again.
    async with session_factory() as session:
        user = (await session.execute(select(UserModel))).scalars().first()
        assert user is not None
        assert user.streak == 0


async def test_a_live_streak_survives_a_day_that_asked_for_nothing(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Nothing due: preserved, not advanced, and never celebrated."""
    await seed(client, auth_headers, ["a"])
    yesterday = await backdate_streak(session_factory, days=1, streak=4)

    body = await overview(client, auth_headers)
    assert body["due_count"] == 0
    assert body["streak"] == 4
    assert body["day_state"] == "rest"

    async with session_factory() as session:
        user = (await session.execute(select(UserModel))).scalars().first()
        assert user is not None
        # The chain moved forward so tomorrow is still consecutive...
        assert user.streak_last_day == date.today()
        # ...but the day was not banked, which is what keeps it uncelebrated
        # and stops it counting as one of the learner's days.
        assert user.streak_banked_on == yesterday


async def test_a_streak_kept_yesterday_is_not_lost_by_looking_today(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(client, auth_headers, ["a", "b"])
    await sleep_on_it(session_factory)
    await backdate_streak(session_factory, days=1, streak=4)

    body = await overview(client, auth_headers)
    assert body["streak"] == 4
    assert body["day_state"] == "open"


async def test_the_profile_settles_the_same_streak_the_overview_does(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Both carry the number, and the client takes whichever arrives last.

    A stale streak on the profile would overwrite a settled one from the
    overview, which is the same bug wearing a different hat.
    """
    await seed(client, auth_headers, ["a", "b"])
    await sleep_on_it(session_factory)
    await backdate_streak(session_factory, days=4, streak=11)

    me = await client.get("/api/v1/users/me", headers=auth_headers)
    assert me.status_code == 200, me.text
    assert me.json()["streak"] == 0


async def test_a_banked_streak_continues_from_a_rest_day(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Rested yesterday, worked today: five, not one.

    The rest day has to keep the chain intact or it would preserve the number
    and break it the following morning, which is worse than not offering rest
    days at all.
    """
    await set_goal(client, auth_headers, 5)
    _, word_ids = await seed(client, auth_headers, [f"w{i}" for i in range(5)])
    await sleep_on_it(session_factory)

    # Banked two days ago, then a rest day yesterday.
    yesterday = date.today() - timedelta(days=1)
    async with session_factory() as session:
        user = (await session.execute(select(UserModel))).scalars().first()
        assert user is not None
        user.streak = 4
        user.streak_last_day = yesterday
        user.streak_banked_on = yesterday - timedelta(days=1)
        await session.commit()

    for word_id in word_ids:
        await grade(client, auth_headers, word_id)

    assert (await overview(client, auth_headers))["streak"] == 5


# ── the contract the client reads ──


async def test_the_overview_carries_the_goal_and_the_day_state(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await set_goal(client, auth_headers, 15)
    body = await overview(client, auth_headers)
    assert body["daily_goal"] == 15
    assert body["day_state"] in {"open", "banked", "rest"}


# ── the race ──


async def test_two_sessions_finishing_together_bank_one_day(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A phone and the PWA, in the same second.

    ``bank_day`` is one guarded statement rather than a read-modify-write, so
    the second finds nothing to update. Check-then-write here would advance the
    streak twice for one day's work.
    """
    await set_goal(client, auth_headers, 5)
    _, word_ids = await seed(client, auth_headers, [f"w{i}" for i in range(5)])
    await sleep_on_it(session_factory)
    for word_id in word_ids:
        await grade(client, auth_headers, word_id)

    results = await asyncio.gather(
        *(client.post("/api/v1/study/session/complete", headers=auth_headers) for _ in range(4)),
        return_exceptions=True,
    )
    for result in results:
        assert not isinstance(result, BaseException), result

    assert (await overview(client, auth_headers))["streak"] == 1
