"""Explore, person-to-person shares, and friends.

The distinction these tests exist to pin down: **saving from Explore takes a
copy, sharing with a person shares the deck.** Getting that backwards would
either let one learner's edit rewrite a stranger's deck, or silently fork a
class into thirty divergent copies.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.models.deck import DeckModel
from tests.api.conftest import UserFactory, bearer


async def create_deck(client: AsyncClient, headers: dict[str, str], name: str = "Deck") -> str:
    response = await client.post("/api/v1/decks", headers=headers, json={"name": name, "hue": 262})
    assert response.status_code == 201, response.text
    deck_id: str = response.json()["id"]
    return deck_id


async def add_word(client: AsyncClient, headers: dict[str, str], deck_id: str, term: str) -> str:
    response = await client.post(
        "/api/v1/words",
        headers=headers,
        json={"deck_id": deck_id, "term": term, "meaning": f"meaning of {term}"},
    )
    assert response.status_code == 201, response.text
    word_id: str = response.json()["id"]
    return word_id


async def named(client: AsyncClient, make_user: UserFactory, phone: str, handle: str) -> str:
    user = await make_user(phone=phone, name=handle.title())
    response = await client.patch(
        "/api/v1/users/me", headers=bearer(user.id), json={"username": handle}
    )
    assert response.status_code == 200, response.text
    return str(user.id)


async def publish(
    session_factory: async_sessionmaker[AsyncSession],
    deck_id: str,
    *,
    category: str = "general",
    official: bool = False,
) -> None:
    """Publishing is admin-only, so tests set the flag directly."""
    async with session_factory() as session:
        await session.execute(
            update(DeckModel)
            .where(DeckModel.id == UUID(deck_id))
            .values(
                is_public=True,
                is_official=official,
                category=category,
                description="A deck",
                description_fa="یک دسته",
                published_at=datetime.now(UTC),
            )
        )
        await session.commit()


# ── Explore ──────────────────────────────────────────────────
async def test_explore_lists_published_decks_only(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    published = await create_deck(client, auth_headers, "Published")
    await add_word(client, auth_headers, published, "improve")
    await create_deck(client, auth_headers, "Private")
    await publish(session_factory, published, category="exam")

    listed = await client.get("/api/v1/decks/public", headers=auth_headers)
    assert listed.status_code == 200
    decks = listed.json()["decks"]
    assert [d["name"] for d in decks] == ["Published"]
    assert decks[0]["word_count"] == 1
    assert decks[0]["category"] == "exam"
    assert decks[0]["saves"] == 0
    # The one place a handle is published, and only for a listed deck.
    assert "author_username" in decks[0]


async def test_explore_filters_by_category_and_query(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    exam = await create_deck(client, auth_headers, "IELTS Core")
    travel = await create_deck(client, auth_headers, "Airport Words")
    await publish(session_factory, exam, category="exam")
    await publish(session_factory, travel, category="travel")

    by_category = await client.get(
        "/api/v1/decks/public", headers=auth_headers, params={"category": "travel"}
    )
    assert [d["name"] for d in by_category.json()["decks"]] == ["Airport Words"]

    by_query = await client.get("/api/v1/decks/public", headers=auth_headers, params={"q": "ielts"})
    assert [d["name"] for d in by_query.json()["decks"]] == ["IELTS Core"]


async def test_importing_takes_a_copy_that_editing_cannot_leak(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    source = await create_deck(client, auth_headers, "Starter")
    unit = (
        await client.post(
            f"/api/v1/decks/{source}/units", headers=auth_headers, json={"name": "Unit 1"}
        )
    ).json()
    original_word = (
        await client.post(
            "/api/v1/words",
            headers=auth_headers,
            json={
                "deck_id": source,
                "term": "improve",
                "meaning": "to get better",
                "phonetic": "/ɪmˈpɹuːv/",
                "unit_id": unit["id"],
            },
        )
    ).json()
    await publish(session_factory, source)

    learner = await make_user(phone="+989121112001")
    imported = await client.post(
        f"/api/v1/decks/public/{source}/import", headers=bearer(learner.id)
    )
    assert imported.status_code == 200, imported.text
    copy_id = imported.json()["id"]
    assert copy_id != source

    # The words and the units came with it.
    copied_words = await client.get(
        "/api/v1/words", headers=bearer(learner.id), params={"deck_id": copy_id}
    )
    assert [w["term"] for w in copied_words.json()] == ["improve"]
    copied_units = await client.get(f"/api/v1/decks/{copy_id}/units", headers=bearer(learner.id))
    assert [u["name"] for u in copied_units.json()] == ["Unit 1"]
    # ...pointing at the *copy's* unit, not the original's.
    assert copied_words.json()[0]["unit_id"] == copied_units.json()[0]["id"]
    assert copied_words.json()[0]["id"] != original_word["id"]
    # The IPA comes with it. How a word is pronounced is a property of the word,
    # not of whose deck it sits in, and re-fetching it per copy would call the
    # dictionary five hundred times for an answer already on the row.
    assert copied_words.json()[0]["phonetic"] == "/ɪmˈpɹuːv/"

    # Editing the copy leaves the original untouched — the whole point.
    await client.patch(
        f"/api/v1/words/{copied_words.json()[0]['id']}",
        headers=bearer(learner.id),
        json={"meaning": "changed"},
    )
    still = await client.get(f"/api/v1/words/{original_word['id']}", headers=auth_headers)
    assert still.json()["meaning"] == "to get better"

    # And the copy is private: one popular deck must not spawn many listings.
    assert [
        d["name"]
        for d in (await client.get("/api/v1/decks/public", headers=auth_headers)).json()["decks"]
    ] == ["Starter"]
    # The save is counted.
    saves = (await client.get("/api/v1/decks/public", headers=auth_headers)).json()["decks"][0]
    assert saves["saves"] == 1


async def test_a_private_deck_cannot_be_imported_by_id(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    private = await create_deck(client, auth_headers, "Private")
    stranger = await make_user(phone="+989121112002")

    response = await client.post(
        f"/api/v1/decks/public/{private}/import", headers=bearer(stranger.id)
    )
    assert response.status_code == 404


# ── person-to-person ─────────────────────────────────────────
async def test_sharing_with_a_person_shares_the_deck_itself(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers, "Book 2")
    word_id = await add_word(client, auth_headers, deck_id, "collaborate")
    await client.patch("/api/v1/users/me", headers=auth_headers, json={"username": "sender_x"})
    friend_id = await named(client, make_user, "+989121112003", "parisa")

    shared = await client.post(
        f"/api/v1/decks/{deck_id}/share", headers=auth_headers, json={"to_username": "parisa"}
    )
    assert shared.status_code == 200, shared.text
    assert len(shared.json()["code"]) == 13  # the deck's invite code, to paste

    offers = await client.get("/api/v1/decks/shared", headers=bearer(friend_id))
    assert offers.status_code == 200
    offer = offers.json()["decks"][0]
    assert offer["name"] == "Book 2"
    assert offer["word_count"] == 1
    assert offer["from_username"] == "sender_x"
    assert offer["accepted"] is False

    # Not theirs until accepted.
    assert (await client.get("/api/v1/decks", headers=bearer(friend_id))).json() == []

    accepted = await client.post(
        f"/api/v1/decks/shared/{offer['id']}/accept", headers=bearer(friend_id)
    )
    assert accepted.status_code == 200
    # The *same* deck, not a copy.
    assert accepted.json()["id"] == deck_id
    assert [
        d["id"] for d in (await client.get("/api/v1/decks", headers=bearer(friend_id))).json()
    ] == [deck_id]

    # An edit by the owner is visible to them, which a copy would not be.
    await client.patch(
        f"/api/v1/words/{word_id}", headers=auth_headers, json={"meaning": "work as one"}
    )
    theirs = await client.get(f"/api/v1/words/{word_id}", headers=bearer(friend_id))
    assert theirs.json()["meaning"] == "work as one"


async def test_declining_removes_the_offer_silently(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers)
    friend_id = await named(client, make_user, "+989121112004", "parisa")
    await client.post(
        f"/api/v1/decks/{deck_id}/share", headers=auth_headers, json={"to_username": "parisa"}
    )
    offer = (await client.get("/api/v1/decks/shared", headers=bearer(friend_id))).json()["decks"][0]

    declined = await client.delete(f"/api/v1/decks/shared/{offer['id']}", headers=bearer(friend_id))
    assert declined.status_code == 204
    assert (await client.get("/api/v1/decks/shared", headers=bearer(friend_id))).json()[
        "decks"
    ] == []
    # They did not become a member, and the sender is told nothing.
    assert (await client.get("/api/v1/decks", headers=bearer(friend_id))).json() == []


async def test_a_share_addressed_to_someone_else_is_invisible(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers)
    friend_id = await named(client, make_user, "+989121112005", "parisa")
    stranger = await make_user(phone="+989121112006")
    await client.post(
        f"/api/v1/decks/{deck_id}/share", headers=auth_headers, json={"to_username": "parisa"}
    )
    offer = (await client.get("/api/v1/decks/shared", headers=bearer(friend_id))).json()["decks"][0]

    # 404 both ways: a share id that is not yours must not be distinguishable
    # from one that does not exist.
    assert (
        await client.post(f"/api/v1/decks/shared/{offer['id']}/accept", headers=bearer(stranger.id))
    ).status_code == 404
    assert (
        await client.delete(f"/api/v1/decks/shared/{offer['id']}", headers=bearer(stranger.id))
    ).status_code == 404


async def test_sharing_is_refused_with_readable_copy(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers)
    await client.patch("/api/v1/users/me", headers=auth_headers, json={"username": "owner_x"})
    await named(client, make_user, "+989121112007", "parisa")

    blank = await client.post(
        f"/api/v1/decks/{deck_id}/share", headers=auth_headers, json={"to_username": " "}
    )
    assert blank.status_code == 422
    assert blank.json()["detail"] == "Enter a handle to share with"

    unknown = await client.post(
        f"/api/v1/decks/{deck_id}/share", headers=auth_headers, json={"to_username": "ghost"}
    )
    assert unknown.json()["detail"] == "No one uses that handle"

    myself = await client.post(
        f"/api/v1/decks/{deck_id}/share", headers=auth_headers, json={"to_username": "owner_x"}
    )
    assert myself.json()["detail"] == "That is your own handle"

    # Already a member.
    await client.post(
        f"/api/v1/decks/{deck_id}/members", headers=auth_headers, json={"username": "parisa"}
    )
    again = await client.post(
        f"/api/v1/decks/{deck_id}/share", headers=auth_headers, json={"to_username": "parisa"}
    )
    assert again.status_code == 409
    assert again.json()["detail"] == "They already have this deck"


async def test_only_the_owner_can_share_a_deck(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    deck_id = await create_deck(client, auth_headers)
    viewer_id = await named(client, make_user, "+989121112008", "viewer_p")
    await named(client, make_user, "+989121112009", "target_p")
    await client.post(
        f"/api/v1/decks/{deck_id}/members", headers=auth_headers, json={"username": "viewer_p"}
    )

    # A viewer handing a teacher's deck around is exactly what roles prevent.
    refused = await client.post(
        f"/api/v1/decks/{deck_id}/share",
        headers=bearer(viewer_id),
        json={"to_username": "target_p"},
    )
    assert refused.status_code == 403


# ── friends ──────────────────────────────────────────────────
async def test_sharing_adds_the_recipient_to_friends(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers)
    await named(client, make_user, "+989121112010", "parisa")

    assert (await client.get("/api/v1/users/me/friends", headers=auth_headers)).json()[
        "friends"
    ] == []
    await client.post(
        f"/api/v1/decks/{deck_id}/share", headers=auth_headers, json={"to_username": "parisa"}
    )

    # The whole point: a handle is typed once.
    friends = await client.get("/api/v1/users/me/friends", headers=auth_headers)
    assert [f["username"] for f in friends.json()["friends"]] == ["parisa"]
    assert friends.json()["friends"][0]["last_shared_at"] is not None


async def test_friends_can_be_added_and_removed_by_hand(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    await named(client, make_user, "+989121112011", "parisa")

    added = await client.post(
        "/api/v1/users/me/friends", headers=auth_headers, json={"username": "Parisa"}
    )
    assert added.status_code == 200, added.text
    assert added.json()["username"] == "parisa"  # normalised
    assert added.json()["last_shared_at"] is None

    # Idempotent: adding twice is not an error.
    assert (
        await client.post(
            "/api/v1/users/me/friends", headers=auth_headers, json={"username": "parisa"}
        )
    ).status_code == 200
    assert (
        len((await client.get("/api/v1/users/me/friends", headers=auth_headers)).json()["friends"])
        == 1
    )

    removed = await client.delete("/api/v1/users/me/friends/parisa", headers=auth_headers)
    assert removed.status_code == 204
    assert (await client.get("/api/v1/users/me/friends", headers=auth_headers)).json()[
        "friends"
    ] == []


async def test_adding_a_friend_reveals_nothing_but_a_name(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    await named(client, make_user, "+989121112012", "parisa")

    body = (
        await client.post(
            "/api/v1/users/me/friends", headers=auth_headers, json={"username": "parisa"}
        )
    ).json()
    # No phone, no email, no stats — the handle was already known to the caller.
    assert set(body) == {"username", "name", "last_shared_at"}


async def test_friend_errors_are_user_facing_copy(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await client.patch("/api/v1/users/me", headers=auth_headers, json={"username": "me_x"})

    blank = await client.post(
        "/api/v1/users/me/friends", headers=auth_headers, json={"username": " "}
    )
    assert blank.status_code == 422
    assert blank.json()["detail"] == "Enter a handle first"

    own = await client.post(
        "/api/v1/users/me/friends", headers=auth_headers, json={"username": "me_x"}
    )
    assert own.json()["detail"] == "That is your own handle"


async def test_friends_require_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/users/me/friends")).status_code == 401
    assert (await client.get("/api/v1/decks/public")).status_code == 401
    assert (await client.get("/api/v1/decks/shared")).status_code == 401


async def test_publishing_is_admin_only(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    """Explore is curated, not self-service.

    There is no report path and no moderation queue yet, so an open publish
    button would be an unreviewed-content problem rather than a feature.
    """
    deck_id = await create_deck(client, auth_headers, "Candidate")

    # The owner cannot publish their own deck.
    refused = await client.patch(
        f"/api/v1/admin/decks/{deck_id}/publish", headers=auth_headers, json={"is_public": True}
    )
    assert refused.status_code == 403
    assert (await client.get("/api/v1/decks/public", headers=auth_headers)).json()["decks"] == []

    admin = await make_user(phone="+989121112090", is_admin=True)
    published = await client.patch(
        f"/api/v1/admin/decks/{deck_id}/publish",
        headers=bearer(admin.id),
        json={"is_public": True, "category": "exam", "description": "Curated"},
    )
    assert published.status_code == 204, published.text

    listed = (await client.get("/api/v1/decks/public", headers=auth_headers)).json()["decks"]
    assert [d["name"] for d in listed] == ["Candidate"]
    assert listed[0]["category"] == "exam"
    assert listed[0]["description"] == "Curated"

    # And it can be taken back out.
    await client.patch(
        f"/api/v1/admin/decks/{deck_id}/publish",
        headers=bearer(admin.id),
        json={"is_public": False},
    )
    assert (await client.get("/api/v1/decks/public", headers=auth_headers)).json()["decks"] == []


async def test_an_official_deck_credits_nobody(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers, "Vocably Starter")
    await client.patch("/api/v1/users/me", headers=auth_headers, json={"username": "staffer"})
    admin = await make_user(phone="+989121112091", is_admin=True)

    await client.patch(
        f"/api/v1/admin/decks/{deck_id}/publish",
        headers=bearer(admin.id),
        json={"is_public": True, "is_official": True},
    )

    listed = (await client.get("/api/v1/decks/public", headers=auth_headers)).json()["decks"][0]
    assert listed["is_official"] is True
    # An official deck is Vocably's, so the staffer's handle is not published.
    assert listed["author_username"] == ""
    assert listed["author_name"] == ""
