"""AI lookup endpoint (through the deterministic stub provider)."""

from __future__ import annotations

from httpx import AsyncClient


async def test_lookup_requires_authentication(client: AsyncClient) -> None:
    response = await client.post("/api/v1/ai/lookup", json={"term": "run"})
    assert response.status_code == 401


async def test_lookup_returns_multiple_senses(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["term"] == "run"
    assert len(body["suggestions"]) == 3
    first = body["suggestions"][0]
    assert set(first) == {"meaning", "context", "example"}
    assert first["context"] == "movement"


async def test_lookup_unknown_word_falls_back_to_generic_sense(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/ai/lookup", headers=auth_headers, json={"term": "serendipity"}
    )
    assert response.status_code == 200
    assert len(response.json()["suggestions"]) == 1


async def test_story_requires_authentication(client: AsyncClient) -> None:
    response = await client.post("/api/v1/ai/story")
    assert response.status_code == 401


async def test_story_requires_enough_learned_words(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post("/api/v1/ai/story", headers=auth_headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_story_uses_learned_words(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    deck = await client.post(
        "/api/v1/decks", headers=auth_headers, json={"name": "Everyday English", "hue": 262}
    )
    terms = ["reliable", "improve", "borrow"]
    for term in terms:
        created = await client.post(
            "/api/v1/words",
            headers=auth_headers,
            json={"deck_id": deck.json()["id"], "term": term, "meaning": f"meaning of {term}"},
        )
        # Two "easy" grades take a new word from box 1 to box 5 (mastered).
        for _ in range(2):
            graded = await client.post(
                f"/api/v1/study/words/{created.json()['id']}/grade",
                headers=auth_headers,
                json={"grade": "easy"},
            )
            assert graded.status_code == 200
        assert graded.json()["box"] == 5

    response = await client.post("/api/v1/ai/story", headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert sorted(body["words_used"]) == sorted(terms)
    assert all(term in body["text"] for term in terms)
