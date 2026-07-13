"""Deck CRUD, stats, and ownership enforcement."""

from __future__ import annotations

from httpx import AsyncClient

from tests.api.conftest import UserFactory, bearer


async def create_deck(
    client: AsyncClient, headers: dict[str, str], name: str = "Everyday English", hue: int = 262
) -> dict[str, object]:
    response = await client.post("/api/v1/decks", headers=headers, json={"name": name, "hue": hue})
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


async def test_deck_crud_roundtrip(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    deck = await create_deck(client, auth_headers)
    deck_id = deck["id"]
    assert deck["name"] == "Everyday English"
    assert deck["hue"] == 262
    assert deck["initial"] == "E"

    got = await client.get(f"/api/v1/decks/{deck_id}", headers=auth_headers)
    assert got.status_code == 200

    patched = await client.patch(
        f"/api/v1/decks/{deck_id}", headers=auth_headers, json={"name": "Travel", "hue": 190}
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Travel"
    assert patched.json()["hue"] == 190

    deleted = await client.delete(f"/api/v1/decks/{deck_id}", headers=auth_headers)
    assert deleted.status_code == 204

    gone = await client.get(f"/api/v1/decks/{deck_id}", headers=auth_headers)
    assert gone.status_code == 404
    assert gone.json()["error"]["code"] == "not_found"


async def test_deck_list_includes_stats(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    deck = await create_deck(client, auth_headers)
    for term in ("reliable", "improve"):
        response = await client.post(
            "/api/v1/words",
            headers=auth_headers,
            json={"deck_id": deck["id"], "term": term, "meaning": f"meaning of {term}"},
        )
        assert response.status_code == 201

    listed = await client.get("/api/v1/decks", headers=auth_headers)
    assert listed.status_code == 200
    (stats,) = listed.json()
    assert stats["word_count"] == 2
    assert stats["due_count"] == 2  # new words are due immediately
    assert stats["progress_pct"] == 20  # both words in box 1 of 5


async def test_deleting_a_deck_deletes_its_words(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    deck = await create_deck(client, auth_headers)
    created = await client.post(
        "/api/v1/words",
        headers=auth_headers,
        json={"deck_id": deck["id"], "term": "reliable", "meaning": "able to be trusted"},
    )
    word_id = created.json()["id"]

    deleted = await client.delete(f"/api/v1/decks/{deck['id']}", headers=auth_headers)
    assert deleted.status_code == 204

    gone = await client.get(f"/api/v1/words/{word_id}", headers=auth_headers)
    assert gone.status_code == 404
    listed = await client.get("/api/v1/words", headers=auth_headers)
    assert listed.json() == []


async def test_deck_of_other_user_is_hidden(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck = await create_deck(client, auth_headers)
    other = await make_user(phone="+989121110000")

    response = await client.get(f"/api/v1/decks/{deck['id']}", headers=bearer(other.id))
    assert response.status_code in (403, 404)
