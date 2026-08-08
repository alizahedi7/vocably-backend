"""Study endpoints: overview stats, due-word sessions, and Leitner grading."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.api.conftest import UserFactory, bearer, sleep_on_it


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


async def test_first_grade_of_the_day_advances_streak_once(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    _, word_ids = await seed_deck_with_words(client, auth_headers, ["a", "b"])

    me = await client.get("/api/v1/users/me", headers=auth_headers)
    assert me.json()["streak"] == 0

    for word_id, expected_streak in zip(word_ids, (1, 1), strict=True):
        graded = await client.post(
            f"/api/v1/study/words/{word_id}/grade", headers=auth_headers, json={"grade": "good"}
        )
        assert graded.status_code == 200
        me = await client.get("/api/v1/users/me", headers=auth_headers)
        assert me.json()["streak"] == expected_streak


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
        "due_deck_count",
        "estimated_minutes",
        "streak",
        "memory_strength",
    } <= body.keys()
    assert len(body["memory_strength"]["distribution"]) == 5
