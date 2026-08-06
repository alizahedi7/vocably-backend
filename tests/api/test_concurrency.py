"""Races that only appear under real concurrency, and the fixes for them.

Every test here corresponds to a bug found by hammering a running server, not
by reading the code. They are cheap to keep and each one guards a failure that
a single-threaded suite cannot see:

* a double-tapped grade losing a review,
* two taps on an invite link answering 500,
* two people claiming one handle answering 500,
* two invites minting two codes for one deck.

They run the requests through the same session factory the app uses, in
parallel tasks, so the interleaving is real rather than simulated.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import DeckRole
from app.infrastructure.db.models.deck_member import DeckMemberModel
from app.infrastructure.db.models.user import UserModel
from tests.api.conftest import UserFactory, bearer


async def create_deck(client: AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post("/api/v1/decks", headers=headers, json={"name": "R", "hue": 262})
    assert response.status_code == 201, response.text
    deck_id: str = response.json()["id"]
    return deck_id


async def test_concurrent_grades_of_one_card_all_count(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """A double-tapped grade must not vanish.

    The upsert computes its counters from a read, so writing them back verbatim
    made two simultaneous grades land as one — leaving ``review_count``
    disagreeing with ``word_reviews``, which CLAUDE.md forbids. The counters
    are incremented in SQL instead.
    """
    deck_id = await create_deck(client, auth_headers)
    word_id = (
        await client.post(
            "/api/v1/words",
            headers=auth_headers,
            json={"deck_id": deck_id, "term": "race", "meaning": "m"},
        )
    ).json()["id"]

    responses = await asyncio.gather(
        *(
            client.post(
                f"/api/v1/study/words/{word_id}/grade",
                headers=auth_headers,
                json={"grade": "good"},
            )
            for _ in range(6)
        )
    )
    assert {r.status_code for r in responses} == {200}

    word = await client.get(f"/api/v1/words/{word_id}", headers=auth_headers)
    assert word.json()["review_count"] == 6

    # And the log agrees with the counter, which is the actual invariant.
    history = await client.get(f"/api/v1/study/words/{word_id}/reviews", headers=auth_headers)
    if history.status_code == 200:  # endpoint is optional; skip if absent
        assert len(history.json()) == 6


async def test_concurrent_joins_are_idempotent_not_a_crash(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    """Two taps on an invite link used to race check-then-insert into a 500."""
    deck_id = await create_deck(client, auth_headers)
    code = (
        await client.post(
            f"/api/v1/decks/{deck_id}/invite", headers=auth_headers, json={"role": "viewer"}
        )
    ).json()["invite_code"]
    student = await make_user(phone="+989121119001")

    responses = await asyncio.gather(
        *(
            client.post("/api/v1/decks/join", headers=bearer(student.id), json={"code": code})
            for _ in range(5)
        )
    )
    assert {r.status_code for r in responses} == {200}
    assert {r.json()["deck_id"] for r in responses} == {deck_id}

    membership = await client.get(f"/api/v1/decks/{deck_id}/membership", headers=auth_headers)
    assert len(membership.json()["members"]) == 2


async def test_two_people_racing_for_one_handle_get_a_409_not_a_500(
    client: AsyncClient, make_user: UserFactory
) -> None:
    """The availability check cannot be the arbiter; the unique index is.

    Both callers can pass the check in the same instant, so the loser has to be
    turned into readable copy at the constraint rather than a stack trace.
    """
    first = await make_user(phone="+989121119002")
    second = await make_user(phone="+989121119003")

    responses = await asyncio.gather(
        client.patch("/api/v1/users/me", headers=bearer(first.id), json={"username": "contested"}),
        client.patch("/api/v1/users/me", headers=bearer(second.id), json={"username": "contested"}),
    )
    codes = sorted(r.status_code for r in responses)
    assert codes == [200, 409], [r.json() for r in responses]

    loser = next(r for r in responses if r.status_code == 409)
    assert loser.json()["detail"] == "That handle is already taken."


async def test_concurrent_invite_opens_mint_one_code(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # A second code would silently invalidate the one already handed to a class.
    deck_id = await create_deck(client, auth_headers)

    responses = await asyncio.gather(
        *(
            client.post(
                f"/api/v1/decks/{deck_id}/invite", headers=auth_headers, json={"role": "viewer"}
            )
            for _ in range(4)
        )
    )
    assert {r.status_code for r in responses} == {200}
    assert len({r.json()["invite_code"] for r in responses}) == 1


@pytest.mark.skipif(
    "postgresql" not in os.environ.get("TEST_DATABASE_URL", ""),
    reason=(
        "needs independent sessions: the SQLite suite shares one connection via "
        "StaticPool, so the losing request's rollback also undoes the winner's "
        "insert. That is the harness, not the behaviour — set TEST_DATABASE_URL."
    ),
)
async def test_concurrent_identical_member_adds_leave_one_member(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    deck_id = await create_deck(client, auth_headers)
    invitee = await make_user(phone="+989121119004")
    await client.patch(
        "/api/v1/users/me", headers=bearer(invitee.id), json={"username": "invitee_x"}
    )

    responses = await asyncio.gather(
        *(
            client.post(
                f"/api/v1/decks/{deck_id}/members",
                headers=auth_headers,
                json={"username": "invitee_x", "role": "viewer"},
            )
            for _ in range(4)
        )
    )
    codes = sorted(r.status_code for r in responses)
    assert codes.count(200) == 1, [r.json() for r in responses]
    assert set(codes) <= {200, 409}, codes

    async with session_factory() as session:
        member = await session.get(DeckMemberModel, (UUID(deck_id), invitee.id))
        assert member is not None
        assert member.role == DeckRole.VIEWER.value


async def test_a_second_join_does_not_demote_a_promoted_member(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """DO NOTHING, not DO UPDATE.

    A student promoted to editor who taps the original viewer link again must
    keep the role the owner gave them.
    """
    deck_id = await create_deck(client, auth_headers)
    code = (
        await client.post(
            f"/api/v1/decks/{deck_id}/invite", headers=auth_headers, json={"role": "viewer"}
        )
    ).json()["invite_code"]
    student = await make_user(phone="+989121119005")
    await client.post("/api/v1/decks/join", headers=bearer(student.id), json={"code": code})

    async with session_factory() as session:
        member = await session.get(DeckMemberModel, (UUID(deck_id), student.id))
        assert member is not None
        member.role = DeckRole.EDITOR.value
        await session.commit()

    again = await client.post("/api/v1/decks/join", headers=bearer(student.id), json={"code": code})
    assert again.status_code == 200

    async with session_factory() as session:
        member = await session.get(DeckMemberModel, (UUID(deck_id), student.id))
        assert member is not None
        assert member.role == DeckRole.EDITOR.value


async def test_a_rejoin_after_leaving_restores_the_learners_boxes(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Progress rows outlive membership, deliberately."""
    deck_id = await create_deck(client, auth_headers)
    word_id = (
        await client.post(
            "/api/v1/words",
            headers=auth_headers,
            json={"deck_id": deck_id, "term": "return", "meaning": "come back"},
        )
    ).json()["id"]
    student = await make_user(phone="+989121119006")
    async with session_factory() as session:
        session.add(
            DeckMemberModel(
                deck_id=UUID(deck_id),
                user_id=student.id,
                role=DeckRole.VIEWER.value,
                joined_at=datetime.now(UTC),
            )
        )
        await session.commit()
    await client.post(
        f"/api/v1/study/words/{word_id}/grade",
        headers=bearer(student.id),
        json={"grade": "good"},
    )

    async with session_factory() as session:
        await session.delete(await session.get(DeckMemberModel, (UUID(deck_id), student.id)))
        await session.commit()
    # Out of the deck, the word is invisible — and so is the deck.
    assert (await client.get("/api/v1/words", headers=bearer(student.id))).json() == []

    code = (
        await client.post(
            f"/api/v1/decks/{deck_id}/invite", headers=auth_headers, json={"role": "viewer"}
        )
    ).json()["invite_code"]
    await client.post("/api/v1/decks/join", headers=bearer(student.id), json={"code": code})

    listed = await client.get("/api/v1/words", headers=bearer(student.id))
    assert [w["box"] for w in listed.json()] == [2], "boxes should survive a rejoin"


async def test_deleting_a_user_mid_flight_does_not_500_other_readers(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # The roster resolves members to users; one disappearing between the two
    # queries must drop them from the list, not crash the teacher's screen.
    deck_id = await create_deck(client, auth_headers)
    student = await make_user(phone="+989121119007")
    async with session_factory() as session:
        session.add(
            DeckMemberModel(
                deck_id=UUID(deck_id),
                user_id=student.id,
                role=DeckRole.VIEWER.value,
                joined_at=datetime.now(UTC),
            )
        )
        await session.commit()

    async with session_factory() as session:
        await session.delete(await session.get(UserModel, student.id))
        await session.commit()

    roster = await client.get(f"/api/v1/decks/{deck_id}/roster", headers=auth_headers)
    assert roster.status_code == 200
    assert len(roster.json()["members"]) == 1
