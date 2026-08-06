"""Units: optional grouping inside a deck, and the cards that outlive them."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import DeckRole
from app.infrastructure.db.models.deck_member import DeckMemberModel
from tests.api.conftest import UserFactory, bearer


async def create_deck(client: AsyncClient, headers: dict[str, str], name: str = "Book 2") -> str:
    response = await client.post("/api/v1/decks", headers=headers, json={"name": name, "hue": 262})
    assert response.status_code == 201, response.text
    deck_id: str = response.json()["id"]
    return deck_id


async def create_unit(client: AsyncClient, headers: dict[str, str], deck_id: str, name: str) -> str:
    response = await client.post(
        f"/api/v1/decks/{deck_id}/units", headers=headers, json={"name": name}
    )
    assert response.status_code == 201, response.text
    unit_id: str = response.json()["id"]
    return unit_id


async def test_unit_crud_roundtrip(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    deck_id = await create_deck(client, auth_headers)

    created = await client.post(
        f"/api/v1/decks/{deck_id}/units", headers=auth_headers, json={"name": "  Unit 1  "}
    )
    assert created.status_code == 201, created.text
    unit = created.json()
    assert unit["name"] == "Unit 1"  # stripped
    assert unit["deck_id"] == deck_id
    assert unit["position"] == 0

    renamed = await client.patch(
        f"/api/v1/units/{unit['id']}", headers=auth_headers, json={"name": "Lesson 1"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Lesson 1"

    deleted = await client.delete(f"/api/v1/units/{unit['id']}", headers=auth_headers)
    assert deleted.status_code == 204

    listed = await client.get(f"/api/v1/decks/{deck_id}/units", headers=auth_headers)
    assert listed.json() == []


async def test_units_are_ordered_by_position_not_name(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    deck_id = await create_deck(client, auth_headers)
    for name in ("Unit 1", "Unit 2", "Unit 10"):
        await create_unit(client, auth_headers, deck_id, name)

    listed = await client.get(f"/api/v1/decks/{deck_id}/units", headers=auth_headers)
    assert listed.status_code == 200
    # "Unit 10" sorts between 1 and 2 alphabetically; creation order is the
    # order a teacher built the book in, and is what must come back.
    assert [u["name"] for u in listed.json()] == ["Unit 1", "Unit 2", "Unit 10"]
    assert [u["position"] for u in listed.json()] == [0, 1, 2]


async def test_deleting_a_unit_keeps_its_cards_in_the_deck(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    deck_id = await create_deck(client, auth_headers)
    unit_id = await create_unit(client, auth_headers, deck_id, "Unit 1")
    created = await client.post(
        "/api/v1/words",
        headers=auth_headers,
        json={"deck_id": deck_id, "term": "improve", "meaning": "better", "unit_id": unit_id},
    )
    assert created.status_code == 201, created.text
    assert created.json()["unit_id"] == unit_id

    assert (
        await client.delete(f"/api/v1/units/{unit_id}", headers=auth_headers)
    ).status_code == 204

    # The heading is gone; what was under it is not.
    word = await client.get(f"/api/v1/words/{created.json()['id']}", headers=auth_headers)
    assert word.status_code == 200
    assert word.json()["unit_id"] is None
    assert word.json()["term"] == "improve"


async def test_omitting_unit_id_leaves_it_alone_but_an_explicit_null_clears_it(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    deck_id = await create_deck(client, auth_headers)
    unit_id = await create_unit(client, auth_headers, deck_id, "Unit 1")
    word_id = (
        await client.post(
            "/api/v1/words",
            headers=auth_headers,
            json={"deck_id": deck_id, "term": "borrow", "meaning": "take", "unit_id": unit_id},
        )
    ).json()["id"]

    # Omitted: untouched. This is what stops a client older than units from
    # silently ungrouping every card it edits.
    untouched = await client.patch(
        f"/api/v1/words/{word_id}", headers=auth_headers, json={"meaning": "take temporarily"}
    )
    assert untouched.status_code == 200
    assert untouched.json()["unit_id"] == unit_id

    # Explicit null: removed from its unit.
    cleared = await client.patch(
        f"/api/v1/words/{word_id}", headers=auth_headers, json={"unit_id": None}
    )
    assert cleared.status_code == 200
    assert cleared.json()["unit_id"] is None

    # And back in again.
    restored = await client.patch(
        f"/api/v1/words/{word_id}", headers=auth_headers, json={"unit_id": unit_id}
    )
    assert restored.json()["unit_id"] == unit_id


async def test_a_unit_from_another_deck_is_refused(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    deck_a = await create_deck(client, auth_headers, "Book A")
    deck_b = await create_deck(client, auth_headers, "Book B")
    unit_b = await create_unit(client, auth_headers, deck_b, "B Unit 1")

    # A silent write would put the card under a heading nobody in deck A sees.
    created = await client.post(
        "/api/v1/words",
        headers=auth_headers,
        json={"deck_id": deck_a, "term": "x", "meaning": "y", "unit_id": unit_b},
    )
    assert created.status_code == 422
    assert created.json()["detail"] == "That unit is not part of this deck."


async def test_moving_a_card_to_another_deck_drops_its_unit(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    deck_a = await create_deck(client, auth_headers, "Book A")
    deck_b = await create_deck(client, auth_headers, "Book B")
    unit_a = await create_unit(client, auth_headers, deck_a, "A Unit 1")
    word_id = (
        await client.post(
            "/api/v1/words",
            headers=auth_headers,
            json={"deck_id": deck_a, "term": "x", "meaning": "y", "unit_id": unit_a},
        )
    ).json()["id"]

    # A card cannot keep a unit belonging to the deck it left, and the client
    # does not know to clear it.
    moved = await client.patch(
        f"/api/v1/words/{word_id}", headers=auth_headers, json={"deck_id": deck_b}
    )
    assert moved.status_code == 200
    assert moved.json()["deck_id"] == deck_b
    assert moved.json()["unit_id"] is None


async def test_a_blank_unit_name_is_refused_with_the_clients_copy(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    deck_id = await create_deck(client, auth_headers)
    response = await client.post(
        f"/api/v1/decks/{deck_id}/units", headers=auth_headers, json={"name": "   "}
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Give the unit a name"


async def test_units_require_the_right_role(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    deck_id = await create_deck(client, auth_headers)
    unit_id = await create_unit(client, auth_headers, deck_id, "Unit 1")

    viewer = await make_user(phone="+989121110030")
    stranger = await make_user(phone="+989121110031")
    async with session_factory() as session:
        session.add(
            DeckMemberModel(
                deck_id=UUID(deck_id),
                user_id=viewer.id,
                role=DeckRole.VIEWER.value,
                joined_at=datetime.now(UTC),
            )
        )
        await session.commit()

    # A viewer reads the units and cannot change them.
    assert (
        await client.get(f"/api/v1/decks/{deck_id}/units", headers=bearer(viewer.id))
    ).status_code == 200
    for response in (
        await client.post(
            f"/api/v1/decks/{deck_id}/units", headers=bearer(viewer.id), json={"name": "Mine"}
        ),
        await client.patch(
            f"/api/v1/units/{unit_id}", headers=bearer(viewer.id), json={"name": "M"}
        ),
        await client.delete(f"/api/v1/units/{unit_id}", headers=bearer(viewer.id)),
    ):
        assert response.status_code == 403, response.text

    # A non-member is told nothing at all.
    for response in (
        await client.get(f"/api/v1/decks/{deck_id}/units", headers=bearer(stranger.id)),
        await client.post(
            f"/api/v1/decks/{deck_id}/units", headers=bearer(stranger.id), json={"name": "Mine"}
        ),
        await client.patch(
            f"/api/v1/units/{unit_id}", headers=bearer(stranger.id), json={"name": "M"}
        ),
        await client.delete(f"/api/v1/units/{unit_id}", headers=bearer(stranger.id)),
    ):
        assert response.status_code == 404, response.text

    assert (await client.get(f"/api/v1/decks/{deck_id}/units")).status_code == 401


async def test_a_deck_with_no_units_is_unchanged(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # Most decks will never have a unit; they must render exactly as before.
    deck_id = await create_deck(client, auth_headers)
    created = await client.post(
        "/api/v1/words",
        headers=auth_headers,
        json={"deck_id": deck_id, "term": "plain", "meaning": "simple"},
    )
    assert created.status_code == 201
    assert created.json()["unit_id"] is None
    assert (await client.get(f"/api/v1/decks/{deck_id}/units", headers=auth_headers)).json() == []
