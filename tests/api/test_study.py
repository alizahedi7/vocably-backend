"""Study endpoints: overview stats, due-word sessions, and Leitner grading."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.api.conftest import (
    UserFactory,
    bearer,
    sleep_on_it,
    spread_due_times_over_today,
)


async def seed_deck_with_words(
    client: AsyncClient, headers: dict[str, str], terms: list[str]
) -> tuple[str, list[str]]:
    response = await client.post(
        "/api/v1/decks", headers=headers, json={"name": "Everyday English", "hue": 262}
    )
    deck_id: str = response.json()["id"]
    word_ids: list[str] = []
    for term in terms:
        created = await client.post(
            "/api/v1/words",
            headers=headers,
            json={"deck_id": deck_id, "term": term, "meaning": f"meaning of {term}"},
        )
        assert created.status_code == 201
        word_ids.append(created.json()["id"])
    return deck_id, word_ids


async def test_overview_reflects_box_distribution(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_deck_with_words(client, auth_headers, ["reliable", "improve", "borrow"])

    # Today they are in the boxes but not in the queue: a word is first
    # reviewed the day after it is added.
    same_day = (await client.get("/api/v1/study/overview", headers=auth_headers)).json()
    assert same_day["total_count"] == 3
    assert same_day["due_count"] == 0
    assert same_day["memory_strength"]["total"] == 3

    await sleep_on_it(session_factory)
    response = await client.get("/api/v1/study/overview", headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_count"] == 3
    assert body["due_count"] == 3
    assert body["learned_count"] == 0
    assert body["due_deck_count"] == 1
    assert body["estimated_minutes"] >= 1
    assert body["memory_strength"]["total"] == 3
    by_box = {entry["box"]: entry for entry in body["memory_strength"]["distribution"]}
    assert by_box[1]["count"] == 3
    assert by_box[1]["label"] == "New"
    assert by_box[5]["count"] == 0


async def test_due_count_is_the_same_whatever_the_hour(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The day's queue is a fact about the day, not about the moment asked.

    The reported bug: 10 words in the morning, 13 by the afternoon, 15 by the
    evening, no midnight crossed. Cards were scheduled at the clock time they
    were graded, and read back with ``due_at <= now``, so each one rejoined the
    queue as its own hour came round.

    Here three cards are due at 00:30, 12:00 and 23:30 *today*. All three
    belong to today, so the count is three — at 09:00, at 14:00, at any hour
    the suite runs. Under the old predicate this test reports 1, 2 or 3
    depending on the wall clock, which is the whole complaint.
    """
    _, word_ids = await seed_deck_with_words(client, auth_headers, ["alpha", "beta", "gamma"])
    for word_id in word_ids:
        graded = await client.post(
            f"/api/v1/study/words/{word_id}/grade", headers=auth_headers, json={"grade": "good"}
        )
        assert graded.status_code == 200, graded.text

    await spread_due_times_over_today(session_factory, hours=[0, 12, 23])

    overview = (await client.get("/api/v1/study/overview", headers=auth_headers)).json()
    assert overview["due_count"] == 3

    # And the queue agrees with the number above it — a session that dealt only
    # the cards whose hour had passed was the same bug seen from the other end.
    session = await client.get("/api/v1/study/session", headers=auth_headers)
    assert session.json()["count"] == 3

    # The deck badge is drawn from a different query and must not disagree.
    decks = (await client.get("/api/v1/decks", headers=auth_headers)).json()
    assert decks[0]["due_count"] == 3


async def test_a_card_scheduled_for_tomorrow_stays_there(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The other edge: the day boundary is exclusive.

    Grading a card must not let it back into today's queue, and 00:00 tomorrow
    belongs to tomorrow. Without the strict comparison, every card graded
    ``again`` would reappear in the session it was just answered in.
    """
    _, word_ids = await seed_deck_with_words(client, auth_headers, ["alpha", "beta"])
    for word_id in word_ids:
        await client.post(
            f"/api/v1/study/words/{word_id}/grade", headers=auth_headers, json={"grade": "again"}
        )

    overview = (await client.get("/api/v1/study/overview", headers=auth_headers)).json()
    assert overview["due_count"] == 0

    # Exactly on the boundary — the first instant of tomorrow — is tomorrow.
    await spread_due_times_over_today(session_factory, hours=[24])
    assert (await client.get("/api/v1/study/overview", headers=auth_headers)).json()[
        "due_count"
    ] == 0

    # One minute earlier is today.
    await spread_due_times_over_today(session_factory, hours=[23])
    assert (await client.get("/api/v1/study/overview", headers=auth_headers)).json()[
        "due_count"
    ] == 2


async def test_session_returns_due_words_and_respects_deck_filter(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    deck_id, word_ids = await seed_deck_with_words(client, auth_headers, ["a", "b"])

    session = await client.get("/api/v1/study/session", headers=auth_headers)
    assert session.status_code == 200
    assert session.json()["count"] == 2

    scoped = await client.get(
        "/api/v1/study/session", headers=auth_headers, params={"deck_id": deck_id, "limit": 1}
    )
    assert scoped.status_code == 200
    assert scoped.json()["count"] == 1
    assert scoped.json()["words"][0]["id"] in word_ids


async def test_session_falls_back_to_full_deck_when_nothing_is_due(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    deck_id, word_ids = await seed_deck_with_words(client, auth_headers, ["a", "b"])
    for word_id in word_ids:
        graded = await client.post(
            f"/api/v1/study/words/{word_id}/grade", headers=auth_headers, json={"grade": "easy"}
        )
        assert graded.status_code == 200

    overview = await client.get("/api/v1/study/overview", headers=auth_headers)
    assert overview.json()["due_count"] == 0

    session = await client.get(
        "/api/v1/study/session", headers=auth_headers, params={"deck_id": deck_id}
    )
    assert session.status_code == 200
    assert session.json()["count"] == 2
    assert {w["id"] for w in session.json()["words"]} == set(word_ids)


async def test_grading_moves_boxes_and_schedules_reviews(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    _, (word_id,) = await seed_deck_with_words(client, auth_headers, ["improve"])

    good = await client.post(
        f"/api/v1/study/words/{word_id}/grade", headers=auth_headers, json={"grade": "good"}
    )
    assert good.status_code == 200, good.text
    body = good.json()
    assert body["box"] == 2
    assert body["review_count"] == 1
    assert datetime.fromisoformat(body["due_at"]) > datetime.now(UTC)

    easy = await client.post(
        f"/api/v1/study/words/{word_id}/grade", headers=auth_headers, json={"grade": "easy"}
    )
    assert easy.json()["box"] == 4  # 2 + 2

    again = await client.post(
        f"/api/v1/study/words/{word_id}/grade", headers=auth_headers, json={"grade": "again"}
    )
    assert again.json()["box"] == 1


async def test_grading_unknown_word_is_not_found(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        f"/api/v1/study/words/{uuid4()}/grade", headers=auth_headers, json={"grade": "good"}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_cannot_grade_another_users_word(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    _, (word_id,) = await seed_deck_with_words(client, auth_headers, ["improve"])
    other = await make_user(phone="+989121110000")

    response = await client.post(
        f"/api/v1/study/words/{word_id}/grade", headers=bearer(other.id), json={"grade": "good"}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_grading_a_card_does_not_by_itself_advance_the_streak(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """One card used to bank the day. The goal does now — see test_streak.py.

    These two cards were added today, so they are not even due: answering one
    is practice, and a queue that was never handed out cannot have been
    cleared.
    """
    _, word_ids = await seed_deck_with_words(client, auth_headers, ["a", "b"])

    me = await client.get("/api/v1/users/me", headers=auth_headers)
    assert me.json()["streak"] == 0

    for word_id in word_ids:
        graded = await client.post(
            f"/api/v1/study/words/{word_id}/grade", headers=auth_headers, json={"grade": "good"}
        )
        assert graded.status_code == 200
        me = await client.get("/api/v1/users/me", headers=auth_headers)
        assert me.json()["streak"] == 0


async def test_overview_reports_mastered_count_and_reviewed_today(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """The two fields the badges and the goal ring are built on.

    `mastered_count` is box 5 only — the mastery badges are a pure function of
    it, which is why no badge table exists. `learned_count` counts boxes 4 and
    5 and is deliberately a different number.
    """
    deck_id, word_ids = await seed_deck_with_words(client, auth_headers, ["one", "two"])

    empty = (await client.get("/api/v1/study/overview", headers=auth_headers)).json()
    assert empty["mastered_count"] == 0
    assert empty["reviewed_today"] == 0

    # Four "easy" grades take a card from box 1 to box 5.
    for _ in range(4):
        await client.post(
            f"/api/v1/study/words/{word_ids[0]}/grade",
            headers=auth_headers,
            json={"grade": "easy"},
        )
    await client.post(
        f"/api/v1/study/words/{word_ids[1]}/grade", headers=auth_headers, json={"grade": "good"}
    )

    body = (await client.get("/api/v1/study/overview", headers=auth_headers)).json()
    assert body["mastered_count"] == 1
    assert body["learned_count"] == 1  # box 5 only here; box 4 would also count
    # Five answers today, counted from the rollup rather than the review log.
    assert body["reviewed_today"] == 5


async def test_overview_reports_words_answered_at_least_once(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """`reviewed_count` is the pool the shuffle exercise draws from.

    It is a different question from every other number here, and the box
    distribution cannot answer it: a card graded "again" is back in box 1
    beside the cards nobody has ever seen. Only the counter tells them apart.
    """
    _, word_ids = await seed_deck_with_words(client, auth_headers, ["one", "two", "three"])

    fresh = (await client.get("/api/v1/study/overview", headers=auth_headers)).json()
    # Three words written down, none of them tested.
    assert fresh["total_count"] == 3
    assert fresh["reviewed_count"] == 0

    await client.post(
        f"/api/v1/study/words/{word_ids[0]}/grade", headers=auth_headers, json={"grade": "good"}
    )
    body = (await client.get("/api/v1/study/overview", headers=auth_headers)).json()
    assert body["reviewed_count"] == 1

    # Straight back to box 1, and still a word this learner has met.
    await client.post(
        f"/api/v1/study/words/{word_ids[0]}/grade", headers=auth_headers, json={"grade": "again"}
    )
    back = (await client.get("/api/v1/study/overview", headers=auth_headers)).json()
    assert back["memory_strength"]["distribution"][0]["count"] == 3
    assert back["reviewed_count"] == 1

    # A second card, answered twice: it is words that are counted, not answers.
    for _ in range(2):
        await client.post(
            f"/api/v1/study/words/{word_ids[1]}/grade",
            headers=auth_headers,
            json={"grade": "good"},
        )
    both = (await client.get("/api/v1/study/overview", headers=auth_headers)).json()
    assert both["reviewed_count"] == 2
    assert both["total_count"] == 3


async def test_overview_keys_the_client_hard_casts_are_all_present(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # lib/models/study_overview.dart casts every one of these without a
    # fallback: a missing key crashes the home screen.
    body = (await client.get("/api/v1/study/overview", headers=auth_headers)).json()
    assert {
        "due_count",
        "total_count",
        "learned_count",
        "mastered_count",
        "reviewed_today",
        "reviewed_count",
        "due_deck_count",
        "estimated_minutes",
        "streak",
        "memory_strength",
    } <= body.keys()
    assert len(body["memory_strength"]["distribution"]) == 5
