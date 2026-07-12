"""Study endpoints: overview stats, due-word sessions, and Leitner grading."""

from __future__ import annotations

from datetime import UTC, datetime

from httpx import AsyncClient


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
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await seed_deck_with_words(client, auth_headers, ["reliable", "improve", "borrow"])

    response = await client.get("/api/v1/study/overview", headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_count"] == 3
    assert body["due_count"] == 3  # new words are due immediately
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
