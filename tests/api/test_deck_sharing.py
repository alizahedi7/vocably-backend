"""Sharing a deck: members, roles, the invite link, join, and the roster."""

from __future__ import annotations

from httpx import AsyncClient

from tests.api.conftest import UserFactory, bearer


async def create_deck(client: AsyncClient, headers: dict[str, str], name: str = "Class 5B") -> str:
    response = await client.post("/api/v1/decks", headers=headers, json={"name": name, "hue": 262})
    assert response.status_code == 201, response.text
    deck_id: str = response.json()["id"]
    return deck_id


async def named(client: AsyncClient, make_user: UserFactory, phone: str, handle: str) -> str:
    """A user with a handle, since sharing addresses people by handle."""
    user = await make_user(phone=phone, name=handle.title())
    response = await client.patch(
        "/api/v1/users/me", headers=bearer(user.id), json={"username": handle}
    )
    assert response.status_code == 200, response.text
    return str(user.id)


async def test_membership_is_404_until_the_deck_is_shared(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers)

    # The owner is always a member, so they see a membership of one.
    mine = await client.get(f"/api/v1/decks/{deck_id}/membership", headers=auth_headers)
    assert mine.status_code == 200
    assert mine.json()["my_role"] == "owner"
    assert mine.json()["invite_open"] is False
    assert mine.json()["invite_code"] == ""

    # A non-member gets 404, which the client reads as "not shared" rather
    # than as a failure — and which does not confirm the deck exists.
    stranger = await make_user(phone="+989121110040")
    theirs = await client.get(f"/api/v1/decks/{deck_id}/membership", headers=bearer(stranger.id))
    assert theirs.status_code == 404


async def test_inviting_by_handle_adds_a_member(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers)
    student_id = await named(client, make_user, "+989121110041", "parisa")

    added = await client.post(
        f"/api/v1/decks/{deck_id}/members",
        headers=auth_headers,
        json={"username": "parisa", "role": "viewer"},
    )
    assert added.status_code == 200, added.text
    body = added.json()
    assert len(body["members"]) == 2
    student = next(m for m in body["members"] if m["username"] == "parisa")
    assert student["role"] == "viewer"
    assert student["is_me"] is False
    assert student["progress"] is None  # the roster is cheap, the progress is not

    # And the deck now appears in their own list.
    listed = await client.get("/api/v1/decks", headers=bearer(student_id))
    assert [d["id"] for d in listed.json()] == [deck_id]


async def test_inviting_the_same_person_twice_is_a_conflict_with_readable_copy(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers)
    await named(client, make_user, "+989121110042", "parisa")
    body = {"username": "parisa", "role": "viewer"}

    assert (
        await client.post(f"/api/v1/decks/{deck_id}/members", headers=auth_headers, json=body)
    ).status_code == 200
    again = await client.post(f"/api/v1/decks/{deck_id}/members", headers=auth_headers, json=body)
    assert again.status_code == 409
    assert again.json()["detail"] == "They already have this deck"


async def test_inviting_an_unknown_or_own_handle_is_refused(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    deck_id = await create_deck(client, auth_headers)
    await client.patch("/api/v1/users/me", headers=auth_headers, json={"username": "teacher"})

    nobody = await client.post(
        f"/api/v1/decks/{deck_id}/members", headers=auth_headers, json={"username": "ghost"}
    )
    assert nobody.status_code == 422
    assert nobody.json()["detail"] == "No one uses that handle"

    myself = await client.post(
        f"/api/v1/decks/{deck_id}/members", headers=auth_headers, json={"username": "teacher"}
    )
    assert myself.status_code == 422
    assert myself.json()["detail"] == "That is your own handle"

    blank = await client.post(
        f"/api/v1/decks/{deck_id}/members", headers=auth_headers, json={"username": "  "}
    )
    assert blank.status_code == 422
    assert blank.json()["detail"] == "Enter a handle first"


async def test_the_invite_link_lets_a_class_join_and_keeps_its_code(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers)

    opened = await client.post(
        f"/api/v1/decks/{deck_id}/invite", headers=auth_headers, json={"role": "viewer"}
    )
    assert opened.status_code == 200, opened.text
    code = opened.json()["invite_code"]
    assert opened.json()["invite_open"] is True
    assert opened.json()["invite_role"] == "viewer"
    # A bearer credential: long enough that guessing is not a strategy, and
    # nothing like the client's local hashCode stand-in.
    assert len(code) == 13

    students = [await make_user(phone=f"+98912111005{i}") for i in range(3)]
    for student in students:
        joined = await client.post(
            "/api/v1/decks/join", headers=bearer(student.id), json={"code": code}
        )
        assert joined.status_code == 200, joined.text
        assert joined.json()["deck_id"] == deck_id

    roster = await client.get(f"/api/v1/decks/{deck_id}/roster", headers=auth_headers)
    assert len(roster.json()["members"]) == 4  # teacher + three students

    # Re-opening reuses the row: a code already handed to a class keeps working.
    reopened = await client.post(
        f"/api/v1/decks/{deck_id}/invite", headers=auth_headers, json={"role": "editor"}
    )
    assert reopened.json()["invite_code"] == code
    assert reopened.json()["invite_role"] == "editor"


async def test_joining_twice_is_the_same_deck_not_a_conflict(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers)
    code = (
        await client.post(
            f"/api/v1/decks/{deck_id}/invite", headers=auth_headers, json={"role": "viewer"}
        )
    ).json()["invite_code"]
    student = await make_user(phone="+989121110060")

    for _ in range(2):
        # A learner who taps a link twice has not done anything wrong.
        response = await client.post(
            "/api/v1/decks/join", headers=bearer(student.id), json={"code": code}
        )
        assert response.status_code == 200
        assert response.json()["deck_id"] == deck_id

    membership = await client.get(f"/api/v1/decks/{deck_id}/membership", headers=auth_headers)
    assert len(membership.json()["members"]) == 2


async def test_closing_the_link_stops_new_joins_but_keeps_the_class(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers)
    code = (
        await client.post(
            f"/api/v1/decks/{deck_id}/invite", headers=auth_headers, json={"role": "viewer"}
        )
    ).json()["invite_code"]
    early = await make_user(phone="+989121110061")
    late = await make_user(phone="+989121110062")
    await client.post("/api/v1/decks/join", headers=bearer(early.id), json={"code": code})

    closed = await client.delete(f"/api/v1/decks/{deck_id}/invite", headers=auth_headers)
    assert closed.status_code == 200
    assert closed.json()["invite_open"] is False

    refused = await client.post("/api/v1/decks/join", headers=bearer(late.id), json={"code": code})
    assert refused.status_code == 404
    assert refused.json()["detail"] == "That code does not match an open invite"

    # Revoking a link is not dissolving a class.
    assert (await client.get("/api/v1/decks", headers=bearer(early.id))).json()[0]["id"] == deck_id


async def test_a_bad_code_is_indistinguishable_from_a_closed_one(
    client: AsyncClient, make_user: UserFactory
) -> None:
    # Telling them apart tells someone guessing which codes exist.
    student = await make_user(phone="+989121110063")
    response = await client.post(
        "/api/v1/decks/join", headers=bearer(student.id), json={"code": "ZZZZZZZZZZZZZ"}
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "That code does not match an open invite"


async def test_roles_can_be_changed_and_members_removed(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers)
    student_id = await named(client, make_user, "+989121110064", "parisa")
    await client.post(
        f"/api/v1/decks/{deck_id}/members",
        headers=auth_headers,
        json={"username": "parisa", "role": "viewer"},
    )

    promoted = await client.patch(
        f"/api/v1/decks/{deck_id}/members/parisa", headers=auth_headers, json={"role": "editor"}
    )
    assert promoted.status_code == 200
    assert next(m for m in promoted.json()["members"] if m["username"] == "parisa")["role"] == (
        "editor"
    )

    # Now an editor, they can add a word every member sees.
    added = await client.post(
        "/api/v1/words",
        headers=bearer(student_id),
        json={"deck_id": deck_id, "term": "collaborate", "meaning": "work together"},
    )
    assert added.status_code == 201, added.text

    removed = await client.delete(f"/api/v1/decks/{deck_id}/members/parisa", headers=auth_headers)
    assert removed.status_code == 200
    assert [m["username"] for m in removed.json()["members"]] == [""]

    # The deck is gone from their list; the word they added is not.
    assert (await client.get("/api/v1/decks", headers=bearer(student_id))).json() == []
    assert [
        w["term"] for w in (await client.get("/api/v1/words", headers=auth_headers)).json()
    ] == ["collaborate"]


async def test_nobody_can_promote_themselves_to_owner_or_orphan_the_deck(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers)
    owner_handle = "teacher"
    await client.patch("/api/v1/users/me", headers=auth_headers, json={"username": owner_handle})
    await named(client, make_user, "+989121110065", "parisa")
    await client.post(
        f"/api/v1/decks/{deck_id}/members",
        headers=auth_headers,
        json={"username": "parisa", "role": "editor"},
    )

    # There is exactly one owner: an "owner" role on the wire lands as viewer.
    downgraded = await client.patch(
        f"/api/v1/decks/{deck_id}/members/parisa", headers=auth_headers, json={"role": "owner"}
    )
    assert next(m for m in downgraded.json()["members"] if m["username"] == "parisa")["role"] == (
        "viewer"
    )

    # And the owner cannot remove or demote themselves, which would orphan it.
    assert (
        await client.delete(f"/api/v1/decks/{deck_id}/members/{owner_handle}", headers=auth_headers)
    ).status_code == 422
    assert (
        await client.patch(
            f"/api/v1/decks/{deck_id}/members/{owner_handle}",
            headers=auth_headers,
            json={"role": "viewer"},
        )
    ).status_code == 422


async def test_only_the_owner_manages_members(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers)
    editor_id = await named(client, make_user, "+989121110066", "editor_p")
    await named(client, make_user, "+989121110067", "someone")
    await client.post(
        f"/api/v1/decks/{deck_id}/members",
        headers=auth_headers,
        json={"username": "editor_p", "role": "editor"},
    )
    headers = bearer(editor_id)

    # An editor changes words, not people.
    for response in (
        await client.post(
            f"/api/v1/decks/{deck_id}/members", headers=headers, json={"username": "someone"}
        ),
        await client.patch(
            f"/api/v1/decks/{deck_id}/members/editor_p", headers=headers, json={"role": "viewer"}
        ),
        await client.delete(f"/api/v1/decks/{deck_id}/members/editor_p", headers=headers),
        await client.post(
            f"/api/v1/decks/{deck_id}/invite", headers=headers, json={"role": "viewer"}
        ),
        await client.delete(f"/api/v1/decks/{deck_id}/invite", headers=headers),
    ):
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "permission_denied"


async def test_the_roster_reports_each_members_own_numbers(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers)
    word_ids = [
        (
            await client.post(
                "/api/v1/words",
                headers=auth_headers,
                json={"deck_id": deck_id, "term": term, "meaning": term},
            )
        ).json()["id"]
        for term in ("one", "two", "three")
    ]
    student_id = await named(client, make_user, "+989121110070", "parisa")
    await client.post(
        f"/api/v1/decks/{deck_id}/members",
        headers=auth_headers,
        json={"username": "parisa", "role": "viewer"},
    )

    # The teacher masters one word; the student meets two and masters neither.
    for _ in range(3):
        await client.post(
            f"/api/v1/study/words/{word_ids[0]}/grade", headers=auth_headers, json={"grade": "easy"}
        )
    for word_id in word_ids[:2]:
        await client.post(
            f"/api/v1/study/words/{word_id}/grade",
            headers=bearer(student_id),
            json={"grade": "good"},
        )

    roster = await client.get(f"/api/v1/decks/{deck_id}/roster", headers=auth_headers)
    assert roster.status_code == 200
    members = {m["username"]: m for m in roster.json()["members"]}

    teacher = members[""] if "" in members else next(iter(members.values()))
    student = members["parisa"]

    # Separate progress against the same three cards. Box 4 is deliberately in
    # neither the learning nor the mastered bucket.
    assert student["progress"]["seen"] == 2
    assert student["progress"]["learning"] == 2
    assert student["progress"]["mastered"] == 0
    assert student["progress"]["reviewed_this_week"] == 2
    assert student["progress"]["mastered_this_week"] == 0
    assert student["progress"]["last_active_at"] is not None

    assert teacher["progress"]["seen"] == 1
    assert teacher["progress"]["mastered"] == 1
    assert teacher["progress"]["mastered_this_week"] == 1
    assert teacher["progress"]["reviewed_this_week"] == 3


async def test_a_non_member_reaches_none_of_it(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    deck_id = await create_deck(client, auth_headers)
    stranger = bearer((await make_user(phone="+989121110071")).id)

    for response in (
        await client.get(f"/api/v1/decks/{deck_id}/membership", headers=stranger),
        await client.get(f"/api/v1/decks/{deck_id}/roster", headers=stranger),
        await client.post(
            f"/api/v1/decks/{deck_id}/members", headers=stranger, json={"username": "x"}
        ),
        await client.post(
            f"/api/v1/decks/{deck_id}/invite", headers=stranger, json={"role": "viewer"}
        ),
        await client.delete(f"/api/v1/decks/{deck_id}/invite", headers=stranger),
    ):
        assert response.status_code == 404, response.text

    assert (await client.get(f"/api/v1/decks/{deck_id}/roster")).status_code == 401


async def test_a_viewer_sees_the_roster_including_their_rank(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    # The weekly ranking is the point of the roster, so a member has to be able
    # to read it — what no endpoint exposes is another member's word-level detail.
    deck_id = await create_deck(client, auth_headers)
    student_id = await named(client, make_user, "+989121110072", "parisa")
    await client.post(
        f"/api/v1/decks/{deck_id}/members",
        headers=auth_headers,
        json={"username": "parisa", "role": "viewer"},
    )

    roster = await client.get(f"/api/v1/decks/{deck_id}/roster", headers=bearer(student_id))
    assert roster.status_code == 200
    me = next(m for m in roster.json()["members"] if m["is_me"])
    assert me["username"] == "parisa"
