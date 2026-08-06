"""Experience points: what earns them, and what cannot be claimed.

The award table mirrors ``lib/models/progress_rewards.dart``. A disagreement
shows up as a level that changes when the learner switches device, which is
worse than having no levels at all — so the values are asserted literally here
rather than derived from the enum they came from.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient

from app.domain.entities.xp import XpAction, level_for, total_xp_for


async def create_deck(client: AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post("/api/v1/decks", headers=headers, json={"name": "D", "hue": 262})
    assert response.status_code == 201, response.text
    deck_id: str = response.json()["id"]
    return deck_id


async def add_word(client: AsyncClient, headers: dict[str, str], deck_id: str, term: str) -> str:
    response = await client.post(
        "/api/v1/words", headers=headers, json={"deck_id": deck_id, "term": term, "meaning": "m"}
    )
    assert response.status_code == 201, response.text
    word_id: str = response.json()["id"]
    return word_id


async def xp_of(client: AsyncClient, headers: dict[str, str]) -> int:
    body = (await client.get("/api/v1/users/me", headers=headers)).json()
    value: int = body["xp"]
    return value


# ── the table and the curve ──────────────────────────────────
def test_the_award_table_matches_the_client() -> None:
    assert XpAction.GRADE_WORD.points == 6
    assert XpAction.DRILL_CORRECT.points == 8
    assert XpAction.DRILL_WRONG.points == 3
    assert XpAction.ADD_WORD.points == 5
    assert XpAction.FINISH_SESSION.points == 15
    assert XpAction.DAILY_GOAL.points == 25


@pytest.mark.parametrize(("level", "total"), [(1, 0), (2, 100), (3, 300), (4, 600), (5, 1000)])
def test_the_level_curve_matches_the_client(level: int, total: int) -> None:
    assert total_xp_for(level) == total


@pytest.mark.parametrize(
    ("xp", "level"), [(0, 1), (99, 1), (100, 2), (299, 2), (300, 3), (599, 3), (600, 4)]
)
def test_level_for_matches_the_client(xp: int, level: int) -> None:
    assert level_for(xp) == level


# ── earning it ───────────────────────────────────────────────
async def test_adding_a_word_and_grading_it_both_pay(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    assert await xp_of(client, auth_headers) == 0
    deck_id = await create_deck(client, auth_headers)

    word_id = await add_word(client, auth_headers, deck_id, "improve")
    assert await xp_of(client, auth_headers) == 5

    await client.post(
        f"/api/v1/study/words/{word_id}/grade", headers=auth_headers, json={"grade": "good"}
    )
    assert await xp_of(client, auth_headers) == 5 + 6


async def test_a_drill_pays_differently_and_a_wrong_answer_still_pays(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    deck_id = await create_deck(client, auth_headers)
    word_id = await add_word(client, auth_headers, deck_id, "improve")
    before = await xp_of(client, auth_headers)

    await client.post(
        f"/api/v1/study/words/{word_id}/grade",
        headers=auth_headers,
        json={"grade": "good", "source": "drill"},
    )
    assert await xp_of(client, auth_headers) == before + 8

    # Turning up to be tested on your weakest words is the behaviour worth
    # rewarding, so a wrong answer earns too — just less.
    await client.post(
        f"/api/v1/study/words/{word_id}/grade",
        headers=auth_headers,
        json={"grade": "again", "source": "drill"},
    )
    assert await xp_of(client, auth_headers) == before + 8 + 3


async def test_an_older_client_that_sends_no_source_still_grades_as_a_session(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    deck_id = await create_deck(client, auth_headers)
    word_id = await add_word(client, auth_headers, deck_id, "improve")
    before = await xp_of(client, auth_headers)

    await client.post(
        f"/api/v1/study/words/{word_id}/grade", headers=auth_headers, json={"grade": "good"}
    )
    assert await xp_of(client, auth_headers) == before + XpAction.GRADE_WORD.points


async def test_finishing_a_session_pays_a_bonus_and_reports_the_level(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    before = await xp_of(client, auth_headers)

    finished = await client.post("/api/v1/study/session/complete", headers=auth_headers)
    assert finished.status_code == 200, finished.text
    body = finished.json()
    assert body["awarded"] == 15
    assert body["xp"] == before + 15
    assert body["level"] == level_for(body["xp"])


async def test_the_daily_goal_pays_once_a_day_however_often_it_is_met(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # A goal of 5, so five graded cards crosses it.
    await client.patch("/api/v1/users/me", headers=auth_headers, json={"daily_goal": 5})
    deck_id = await create_deck(client, auth_headers)
    words = [await add_word(client, auth_headers, deck_id, f"w{i}") for i in range(6)]
    after_adding = await xp_of(client, auth_headers)

    for word_id in words[:4]:
        await client.post(
            f"/api/v1/study/words/{word_id}/grade", headers=auth_headers, json={"grade": "good"}
        )
    # Four cards: short of the goal, so grades only.
    assert await xp_of(client, auth_headers) == after_adding + 4 * 6

    await client.post(
        f"/api/v1/study/words/{words[4]}/grade", headers=auth_headers, json={"grade": "good"}
    )
    # The fifth crosses it: the card plus the goal.
    assert await xp_of(client, auth_headers) == after_adding + 5 * 6 + 25

    await client.post(
        f"/api/v1/study/words/{words[5]}/grade", headers=auth_headers, json={"grade": "good"}
    )
    # The sixth is just a card — the goal does not pay twice.
    assert await xp_of(client, auth_headers) == after_adding + 6 * 6 + 25

    # Nor does finishing a session re-trigger it.
    await client.post("/api/v1/study/session/complete", headers=auth_headers)
    assert await xp_of(client, auth_headers) == after_adding + 6 * 6 + 25 + 15


async def test_the_daily_goal_cannot_be_claimed_by_asking(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """There is no endpoint that hands out points on request.

    The goal is derived from the activity rollup server-side; a client that
    says "I met my goal" is not consulted.
    """
    await client.patch("/api/v1/users/me", headers=auth_headers, json={"daily_goal": 20})
    before = await xp_of(client, auth_headers)

    # Finishing a session without doing the work pays the finish bonus only.
    await client.post("/api/v1/study/session/complete", headers=auth_headers)
    assert await xp_of(client, auth_headers) == before + 15


async def test_concurrent_grades_do_not_lose_or_double_award(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # The counter is incremented in SQL for the same reason the review counters
    # are: two awards landing together must not overwrite one another.
    await client.patch("/api/v1/users/me", headers=auth_headers, json={"daily_goal": 20})
    deck_id = await create_deck(client, auth_headers)
    words = [await add_word(client, auth_headers, deck_id, f"w{i}") for i in range(5)]
    before = await xp_of(client, auth_headers)

    await asyncio.gather(
        *(
            client.post(
                f"/api/v1/study/words/{word_id}/grade",
                headers=auth_headers,
                json={"grade": "good"},
            )
            for word_id in words
        )
    )
    assert await xp_of(client, auth_headers) == before + 5 * 6


async def test_xp_is_not_writable_through_the_profile(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # An endpoint that accepts "give me N points" is an endpoint that hands out
    # N points. PATCH ignores it entirely.
    await client.patch("/api/v1/users/me", headers=auth_headers, json={"xp": 999_999})
    assert await xp_of(client, auth_headers) == 0
