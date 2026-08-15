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


async def test_a_public_deck_can_be_read_before_it_is_saved(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The preview: sections from the detail endpoint, cards from the words one.

    Saving a five-hundred-word deck is a decision about the next month of
    someone's studying. Before this, the only way to see what was in one was to
    take it.
    """
    source = await create_deck(client, auth_headers, "Coursebook")
    lesson_one = (
        await client.post(
            f"/api/v1/decks/{source}/units", headers=auth_headers, json={"name": "Lesson 1"}
        )
    ).json()
    lesson_two = (
        await client.post(
            f"/api/v1/decks/{source}/units", headers=auth_headers, json={"name": "Lesson 2"}
        )
    ).json()
    for unit, term in ((lesson_one, "abandon"), (lesson_one, "abate"), (lesson_two, "candid")):
        await client.post(
            "/api/v1/words",
            headers=auth_headers,
            json={
                "deck_id": source,
                "term": term,
                "meaning": f"meaning of {term}",
                "unit_id": unit["id"],
            },
        )
    # A card in no section at all, which the preview must not lose.
    await add_word(client, auth_headers, source, "loose")
    await publish(session_factory, source)

    stranger = await make_user(phone="+989121112010")
    headers = bearer(stranger.id)

    detail = await client.get(f"/api/v1/decks/public/{source}", headers=headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["name"] == "Coursebook"
    assert body["word_count"] == 4
    # Sections in the author's order, each carrying its own count — the shape of
    # the book, without fetching the book.
    assert [(u["name"], u["word_count"]) for u in body["units"]] == [
        ("Lesson 1", 2),
        ("Lesson 2", 1),
    ]
    # Not saved: nobody has taken a copy.
    assert body["saved"] is False

    # Every card, oldest first — the order the book is meant to be worked in.
    # Compared as a set: four cards written inside one test share a timestamp,
    # so the tie-break is the uuid and the *sequence* here says nothing. What
    # the order has to be is stable, which the paging assertion below pins.
    words = await client.get(f"/api/v1/decks/public/{source}/words", headers=headers)
    assert words.status_code == 200, words.text
    order = [w["term"] for w in words.json()]
    assert sorted(order) == ["abandon", "abate", "candid", "loose"]
    # The content, and nothing that would be a claim about progress this reader
    # does not have.
    assert set(words.json()[0]) == {
        "id",
        "unit_id",
        "term",
        "meaning",
        "definition",
        "example",
        "phonetic",
    }

    # One section at a time is what the preview actually asks for.
    just_one = await client.get(
        f"/api/v1/decks/public/{source}/words",
        headers=headers,
        params={"unit_id": lesson_one["id"]},
    )
    assert sorted(w["term"] for w in just_one.json()) == ["abandon", "abate"]

    # And it pages, so a deck's size is never a constant to keep in step. The
    # pages have to be the one list cut in two: a page boundary that shuffles
    # drops a card silently, which is exactly the bug `GET /words` once had.
    page = await client.get(
        f"/api/v1/decks/public/{source}/words",
        headers=headers,
        params={"limit": 2, "offset": 2},
    )
    assert [w["term"] for w in page.json()] == order[2:]


async def test_a_private_deck_cannot_be_previewed_by_id(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    """The preview is the one place card content leaves a deck's membership.

    Which is only acceptable because the owner published it — so an unpublished
    deck must 404 here exactly as it does at import, or the endpoint becomes a
    way to read anyone's deck by id.
    """
    private = await create_deck(client, auth_headers, "Private")
    await add_word(client, auth_headers, private, "secret")
    stranger = await make_user(phone="+989121112011")

    assert (
        await client.get(f"/api/v1/decks/public/{private}", headers=bearer(stranger.id))
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/decks/public/{private}/words", headers=bearer(stranger.id))
    ).status_code == 404


async def test_explore_says_which_decks_the_learner_already_saved(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The tick on the Save button, answered by the server rather than the phone.

    Remembering it on the device is wrong after a reinstall, wrong on a second
    phone, and wrong in the PWA the same account is signed into.
    """
    taken = await create_deck(client, auth_headers, "Taken")
    untaken = await create_deck(client, auth_headers, "Untaken")
    await publish(session_factory, taken)
    await publish(session_factory, untaken)

    learner = await make_user(phone="+989121112012")
    headers = bearer(learner.id)

    before = (await client.get("/api/v1/decks/public", headers=headers)).json()["decks"]
    assert {d["name"]: d["saved"] for d in before} == {"Taken": False, "Untaken": False}

    await client.post(f"/api/v1/decks/public/{taken}/import", headers=headers)

    after = (await client.get("/api/v1/decks/public", headers=headers)).json()["decks"]
    assert {d["name"]: d["saved"] for d in after} == {"Taken": True, "Untaken": False}
    # The detail endpoint agrees, so the preview's own button is not the one
    # control in the app that forgets.
    detail = await client.get(f"/api/v1/decks/public/{taken}", headers=headers)
    assert detail.json()["saved"] is True

    # It is the *learner's* copy that counts, not anyone's.
    someone_else = await make_user(phone="+989121112013")
    theirs = (await client.get("/api/v1/decks/public", headers=bearer(someone_else.id))).json()[
        "decks"
    ]
    assert {d["name"]: d["saved"] for d in theirs} == {"Taken": False, "Untaken": False}


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
    # Nothing to paste: this deck has no open link, and naming one person is
    # not a request for a public one. See the invite-link test below.
    assert shared.json()["code"] == ""

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
    # And it has left the inbox: the deck is theirs now, so the offer has
    # nothing left to ask. It used to stay, as a card saying it had been taken.
    assert (await client.get("/api/v1/decks/shared", headers=bearer(friend_id))).json()[
        "decks"
    ] == []

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


async def test_being_removed_from_a_deck_leaves_no_card_in_the_inbox(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    """The bug this is here to stop: a deck you can no longer open, still listed.

    The share row used to survive acceptance, so the recipient's Shared tab
    kept a card for a deck they had taken — and kept it after the owner removed
    them, when they had no access to it at all. An offer must never outlive the
    membership it created.
    """
    deck_id = await create_deck(client, auth_headers, "Class set")
    friend_id = await named(client, make_user, "+989121112011", "parisa")
    await client.post(
        f"/api/v1/decks/{deck_id}/share", headers=auth_headers, json={"to_username": "parisa"}
    )
    offer = (await client.get("/api/v1/decks/shared", headers=bearer(friend_id))).json()["decks"][0]
    assert (
        await client.post(f"/api/v1/decks/shared/{offer['id']}/accept", headers=bearer(friend_id))
    ).status_code == 200

    removed = await client.delete(f"/api/v1/decks/{deck_id}/members/parisa", headers=auth_headers)
    assert removed.status_code in (200, 204), removed.text

    # Gone from both lists — not theirs, and not offered to them either.
    assert (await client.get("/api/v1/decks", headers=bearer(friend_id))).json() == []
    assert (await client.get("/api/v1/decks/shared", headers=bearer(friend_id))).json()[
        "decks"
    ] == []


async def test_re_sharing_after_a_removal_arrives_as_a_fresh_offer(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    """Inviting somebody back has to reach them.

    With the offer flagged rather than deleted, the upsert met a row already
    marked accepted and left it that way — so the second invitation was written
    to the database and never shown to anyone.
    """
    deck_id = await create_deck(client, auth_headers, "Class set")
    friend_id = await named(client, make_user, "+989121112012", "parisa")
    await client.post(
        f"/api/v1/decks/{deck_id}/share", headers=auth_headers, json={"to_username": "parisa"}
    )
    offer = (await client.get("/api/v1/decks/shared", headers=bearer(friend_id))).json()["decks"][0]
    await client.post(f"/api/v1/decks/shared/{offer['id']}/accept", headers=bearer(friend_id))
    await client.delete(f"/api/v1/decks/{deck_id}/members/parisa", headers=auth_headers)

    again = await client.post(
        f"/api/v1/decks/{deck_id}/share",
        headers=auth_headers,
        json={"to_username": "parisa", "role": "editor"},
    )
    assert again.status_code == 200, again.text

    inbox = (await client.get("/api/v1/decks/shared", headers=bearer(friend_id))).json()["decks"]
    assert [d["name"] for d in inbox] == ["Class set"]
    assert inbox[0]["accepted"] is False


async def test_an_offer_overtaken_by_an_invite_code_stops_being_asked(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    """Joining by link answers the question the pending offer was asking.

    Nothing deletes the offer in this path — the recipient never touched it —
    so the inbox has to check membership as well as the flag, or it asks
    somebody to accept a deck they are already studying.
    """
    deck_id = await create_deck(client, auth_headers, "Book 3")
    friend_id = await named(client, make_user, "+989121112013", "parisa")
    await client.post(
        f"/api/v1/decks/{deck_id}/share", headers=auth_headers, json={"to_username": "parisa"}
    )
    invite = await client.post(
        f"/api/v1/decks/{deck_id}/invite", headers=auth_headers, json={"role": "viewer"}
    )
    assert invite.status_code == 200, invite.text

    joined = await client.post(
        "/api/v1/decks/join", headers=bearer(friend_id), json={"code": invite.json()["invite_code"]}
    )
    assert joined.status_code == 200, joined.text

    assert (await client.get("/api/v1/decks/shared", headers=bearer(friend_id))).json()[
        "decks"
    ] == []


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


async def test_sharing_with_a_handle_never_opens_the_invite_link(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    """An invite code is a bearer credential over the whole deck.

    Sharing used to mint one and switch it on so the sharer had something to
    paste, which meant naming a single student by handle left a live public
    join link on the deck that nobody had asked for — and re-opened, at the
    next share, a link the owner had deliberately closed. Both directions are
    checked here: silent while there is no link, honest once there is one.
    """
    deck_id = await create_deck(client, auth_headers, "Book 3")
    await client.patch("/api/v1/users/me", headers=auth_headers, json={"username": "sender_y"})
    await named(client, make_user, "+989121112010", "parisa")
    await named(client, make_user, "+989121112011", "nima")

    shared = await client.post(
        f"/api/v1/decks/{deck_id}/share", headers=auth_headers, json={"to_username": "parisa"}
    )
    assert shared.status_code == 200, shared.text
    assert shared.json()["code"] == ""

    # The deck is not reachable by a link, and does not claim to be: this is
    # what the client's header chip reads to decide there is no study group.
    membership = await client.get(f"/api/v1/decks/{deck_id}/membership", headers=auth_headers)
    assert membership.status_code == 200, membership.text
    assert membership.json()["invite_open"] is False
    assert membership.json()["invite_code"] == ""

    # Opening the link is the owner's own decision, made with its own control.
    opened = await client.post(
        f"/api/v1/decks/{deck_id}/invite", headers=auth_headers, json={"role": "viewer"}
    )
    assert opened.status_code == 200, opened.text
    code = opened.json()["invite_code"]
    assert len(code) == 13

    # And now that there *is* something to paste, sharing hands it back —
    # the same code, not a new one, so a link already given to a class keeps
    # working.
    again = await client.post(
        f"/api/v1/decks/{deck_id}/share", headers=auth_headers, json={"to_username": "nima"}
    )
    assert again.status_code == 200, again.text
    assert again.json()["code"] == code

    # Closing it is respected: the next share must not turn it back on.
    closed = await client.delete(f"/api/v1/decks/{deck_id}/invite", headers=auth_headers)
    assert closed.status_code == 200, closed.text
    await named(client, make_user, "+989121112012", "sara")
    after = await client.post(
        f"/api/v1/decks/{deck_id}/share", headers=auth_headers, json={"to_username": "sara"}
    )
    assert after.status_code == 200, after.text
    assert after.json()["code"] == ""
    reread = await client.get(f"/api/v1/decks/{deck_id}/membership", headers=auth_headers)
    assert reread.json()["invite_open"] is False


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


async def test_a_share_carries_the_role_it_was_sent_at(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers, "Class set")
    friend_id = await named(client, make_user, "+989121112010", "parisa")

    sent = await client.post(
        f"/api/v1/decks/{deck_id}/share",
        headers=auth_headers,
        json={"to_username": "parisa", "role": "editor"},
    )
    assert sent.status_code == 200, sent.text

    offer = (await client.get("/api/v1/decks/shared", headers=bearer(friend_id))).json()["decks"][0]
    await client.post(f"/api/v1/decks/shared/{offer['id']}/accept", headers=bearer(friend_id))

    # Accepting is what applies it — the role rode on the offer, not on a
    # membership created before they had answered.
    membership = await client.get(f"/api/v1/decks/{deck_id}/membership", headers=bearer(friend_id))
    assert membership.json()["my_role"] == "editor"


async def test_a_share_cannot_hand_over_ownership(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers)
    friend_id = await named(client, make_user, "+989121112011", "parisa")
    await client.post(
        f"/api/v1/decks/{deck_id}/share",
        headers=auth_headers,
        json={"to_username": "parisa", "role": "owner"},
    )
    offer = (await client.get("/api/v1/decks/shared", headers=bearer(friend_id))).json()["decks"][0]
    await client.post(f"/api/v1/decks/shared/{offer['id']}/accept", headers=bearer(friend_id))

    membership = await client.get(f"/api/v1/decks/{deck_id}/membership", headers=bearer(friend_id))
    assert membership.json()["my_role"] == "viewer"


async def test_a_share_defaults_to_viewer_for_a_client_that_sends_no_role(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers)
    await named(client, make_user, "+989121112012", "parisa")
    await client.post(
        f"/api/v1/decks/{deck_id}/share", headers=auth_headers, json={"to_username": "parisa"}
    )
    pending = await client.get(f"/api/v1/decks/{deck_id}/shares", headers=auth_headers)
    assert pending.json()["shares"][0]["role"] == "viewer"


async def test_pending_shares_list_who_has_been_asked_and_not_yet_answered(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers)
    parisa_id = await named(client, make_user, "+989121112013", "parisa")
    await named(client, make_user, "+989121112014", "reza")

    for handle in ("parisa", "reza"):
        await client.post(
            f"/api/v1/decks/{deck_id}/share", headers=auth_headers, json={"to_username": handle}
        )

    pending = await client.get(f"/api/v1/decks/{deck_id}/shares", headers=auth_headers)
    assert pending.status_code == 200, pending.text
    shares = pending.json()["shares"]
    assert [s["username"] for s in shares] == ["parisa", "reza"]
    assert shares[0]["name"] == "Parisa"

    # Accepting moves someone from "asked" to "in the deck": the roster is
    # where a member is reported, and saying it in both places is what makes a
    # share sheet confusing.
    offer = (await client.get("/api/v1/decks/shared", headers=bearer(parisa_id))).json()["decks"][0]
    await client.post(f"/api/v1/decks/shared/{offer['id']}/accept", headers=bearer(parisa_id))

    after = await client.get(f"/api/v1/decks/{deck_id}/shares", headers=auth_headers)
    assert [s["username"] for s in after.json()["shares"]] == ["reza"]
    roster = await client.get(f"/api/v1/decks/{deck_id}/roster", headers=auth_headers)
    assert "parisa" in [m["username"] for m in roster.json()["members"]]


async def test_a_declined_share_stops_being_pending_so_it_can_be_offered_again(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers)
    friend_id = await named(client, make_user, "+989121112015", "parisa")
    await client.post(
        f"/api/v1/decks/{deck_id}/share", headers=auth_headers, json={"to_username": "parisa"}
    )
    offer = (await client.get("/api/v1/decks/shared", headers=bearer(friend_id))).json()["decks"][0]
    await client.delete(f"/api/v1/decks/shared/{offer['id']}", headers=bearer(friend_id))

    pending = await client.get(f"/api/v1/decks/{deck_id}/shares", headers=auth_headers)
    assert pending.json()["shares"] == []


async def test_only_someone_who_may_invite_can_see_who_was_invited(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers)
    outsider = await make_user(phone="+989121112016", name="Outsider")
    denied = await client.get(f"/api/v1/decks/{deck_id}/shares", headers=bearer(outsider.id))
    assert denied.status_code in (403, 404)
