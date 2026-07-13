"""Word CRUD and ownership enforcement."""

from __future__ import annotations

from httpx import AsyncClient

from tests.api.conftest import UserFactory, bearer


async def create_deck(client: AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post(
        "/api/v1/decks", headers=headers, json={"name": "Everyday English", "hue": 262}
    )
    assert response.status_code == 201
    deck_id: str = response.json()["id"]
    return deck_id


async def test_word_crud_roundtrip(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    deck_id = await create_deck(client, auth_headers)

    created = await client.post(
        "/api/v1/words",
        headers=auth_headers,
        json={
            "deck_id": deck_id,
            "term": " reliable ",
            "meaning": "able to be trusted",
            "example": "She is a reliable friend.",
            "sense_label": "adjective · character",
        },
    )
    assert created.status_code == 201, created.text
    word = created.json()
    assert word["term"] == "reliable"  # stripped
    assert word["box"] == 1
    assert word["sense_label"] == "adjective · character"

    listed = await client.get(
        "/api/v1/words", headers=auth_headers, params={"deck_id": deck_id}
    )
    assert listed.status_code == 200
    assert [w["id"] for w in listed.json()] == [word["id"]]

    patched = await client.patch(
        f"/api/v1/words/{word['id']}",
        headers=auth_headers,
        json={"meaning": "can be counted on", "sense_label": "my definition"},
    )
    assert patched.status_code == 200
    assert patched.json()["meaning"] == "can be counted on"
    assert patched.json()["sense_label"] == "my definition"

    deleted = await client.delete(f"/api/v1/words/{word['id']}", headers=auth_headers)
    assert deleted.status_code == 204

    gone = await client.get(f"/api/v1/words/{word['id']}", headers=auth_headers)
    assert gone.status_code == 404


async def test_word_can_move_between_decks(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    deck_a = await create_deck(client, auth_headers)
    response = await client.post(
        "/api/v1/decks", headers=auth_headers, json={"name": "Work", "hue": 55}
    )
    deck_b = response.json()["id"]

    created = await client.post(
        "/api/v1/words",
        headers=auth_headers,
        json={"deck_id": deck_a, "term": "deadline", "meaning": "latest time to finish"},
    )
    word_id = created.json()["id"]

    moved = await client.patch(
        f"/api/v1/words/{word_id}", headers=auth_headers, json={"deck_id": deck_b}
    )
    assert moved.status_code == 200
    assert moved.json()["deck_id"] == deck_b


async def test_oversized_text_fields_are_rejected(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    deck_id = await create_deck(client, auth_headers)

    created = await client.post(
        "/api/v1/words",
        headers=auth_headers,
        json={"deck_id": deck_id, "term": "flood", "meaning": "x" * 2001},
    )
    assert created.status_code == 422

    word = await client.post(
        "/api/v1/words",
        headers=auth_headers,
        json={"deck_id": deck_id, "term": "flood", "meaning": "an overflow of water"},
    )
    patched = await client.patch(
        f"/api/v1/words/{word.json()['id']}",
        headers=auth_headers,
        json={"example": "y" * 2001},
    )
    assert patched.status_code == 422


async def test_patch_updates_term_and_clears_example(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    deck_id = await create_deck(client, auth_headers)
    created = await client.post(
        "/api/v1/words",
        headers=auth_headers,
        json={
            "deck_id": deck_id,
            "term": "reliable",
            "meaning": "able to be trusted",
            "example": "She is a reliable friend.",
        },
    )
    word_id = created.json()["id"]

    patched = await client.patch(
        f"/api/v1/words/{word_id}",
        headers=auth_headers,
        json={"term": " dependable ", "example": ""},
    )
    assert patched.status_code == 200
    assert patched.json()["term"] == "dependable"  # stripped
    assert patched.json()["example"] is None  # empty string clears it


async def test_cannot_move_word_into_another_users_deck(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers)
    created = await client.post(
        "/api/v1/words",
        headers=auth_headers,
        json={"deck_id": deck_id, "term": "deadline", "meaning": "latest time to finish"},
    )
    other = await make_user(phone="+989121110000")
    foreign_deck = await client.post(
        "/api/v1/decks", headers=bearer(other.id), json={"name": "Theirs", "hue": 25}
    )

    response = await client.patch(
        f"/api/v1/words/{created.json()['id']}",
        headers=auth_headers,
        json={"deck_id": foreign_deck.json()["id"]},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


async def test_cannot_create_word_in_another_users_deck(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers)
    other = await make_user(phone="+989121110000")

    response = await client.post(
        "/api/v1/words",
        headers=bearer(other.id),
        json={"deck_id": deck_id, "term": "steal", "meaning": "should not work"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


async def test_cannot_read_another_users_word(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers)
    created = await client.post(
        "/api/v1/words",
        headers=auth_headers,
        json={"deck_id": deck_id, "term": "borrow", "meaning": "take temporarily"},
    )
    other = await make_user(phone="+989121110000")

    response = await client.get(
        f"/api/v1/words/{created.json()['id']}", headers=bearer(other.id)
    )
    assert response.status_code == 403
