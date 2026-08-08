"""Erasing an account: what goes, what survives, and what refuses.

This exists because the first cut of the words/word_progress split made
``words.created_by_user_id`` ``ON DELETE RESTRICT``, which meant **no account
could be deleted at all** — Postgres checks RESTRICT before the deck cascade
removes the user's own cards, so it fired for every learner who had ever added
a word. These are the tests that would have caught it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import DeckRole
from app.infrastructure.db.models.deck import DeckModel
from app.infrastructure.db.models.deck_member import DeckMemberModel
from app.infrastructure.db.models.user import UserModel
from app.infrastructure.db.models.word import WordModel
from app.infrastructure.db.models.word_progress import WordProgressModel
from tests.api.conftest import UserFactory, bearer


async def create_deck(client: AsyncClient, headers: dict[str, str], name: str = "Mine") -> str:
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


async def join(
    session_factory: async_sessionmaker[AsyncSession],
    deck_id: str,
    user_id: UUID,
    role: DeckRole = DeckRole.VIEWER,
) -> None:
    async with session_factory() as session:
        session.add(
            DeckMemberModel(
                deck_id=UUID(deck_id),
                user_id=user_id,
                role=role.value,
                joined_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def test_a_solo_learner_can_erase_their_account(
    client: AsyncClient,
    auth_headers: dict[str, str],
    user: UserModel,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The ordinary case, and the one RESTRICT broke outright."""
    deck_id = await create_deck(client, auth_headers)
    word_id = await add_word(client, auth_headers, deck_id, "improve")
    await client.post(
        f"/api/v1/study/words/{word_id}/grade", headers=auth_headers, json={"grade": "good"}
    )

    deleted = await client.delete("/api/v1/users/me", headers=auth_headers)
    assert deleted.status_code == 204, deleted.text

    async with session_factory() as session:
        assert await session.get(UserModel, user.id) is None
        assert await session.get(DeckModel, UUID(deck_id)) is None
        assert await session.get(WordModel, UUID(word_id)) is None
        assert await session.get(WordProgressModel, (user.id, UUID(word_id))) is None

    # The token now names a user who no longer exists.
    assert (await client.get("/api/v1/users/me", headers=auth_headers)).status_code == 401


async def test_owning_a_shared_deck_refuses_deletion_with_the_deck_named(
    client: AsyncClient,
    auth_headers: dict[str, str],
    user: UserModel,
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    deck_id = await create_deck(client, auth_headers, "Class 5B")
    await add_word(client, auth_headers, deck_id, "improve")
    student = await make_user(phone="+989121110080")
    await join(session_factory, deck_id, student.id)

    refused = await client.delete("/api/v1/users/me", headers=auth_headers)
    assert refused.status_code == 409, refused.text
    # User-visible copy, and it names the deck so the choice is actionable.
    assert "Class 5B" in refused.json()["detail"]
    assert refused.json()["error"]["code"] == "conflict"

    # Nothing was destroyed on the way to refusing.
    async with session_factory() as session:
        assert await session.get(UserModel, user.id) is not None
        assert await session.get(DeckModel, UUID(deck_id)) is not None

    # Once the class is empty again, the account can go.
    await client.delete(f"/api/v1/decks/{deck_id}", headers=auth_headers)
    assert (await client.delete("/api/v1/users/me", headers=auth_headers)).status_code == 204


async def test_a_members_deletion_leaves_the_class_deck_standing(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A card an editor wrote belongs to the deck, not to the editor."""
    deck_id = await create_deck(client, auth_headers, "Class 5B")
    editor = await make_user(phone="+989121110081")
    await join(session_factory, deck_id, editor.id, DeckRole.EDITOR)

    word_id = await add_word(client, bearer(editor.id), deck_id, "collaborate")
    await client.post(
        f"/api/v1/study/words/{word_id}/grade",
        headers=bearer(editor.id),
        json={"grade": "good"},
    )

    assert (await client.delete("/api/v1/users/me", headers=bearer(editor.id))).status_code == 204

    # The deck and the card survive; only the attribution and their own
    # progress are gone.
    async with session_factory() as session:
        word = await session.get(WordModel, UUID(word_id))
        assert word is not None
        assert word.created_by_user_id is None
        assert await session.get(WordProgressModel, (editor.id, UUID(word_id))) is None

    still_there = await client.get(f"/api/v1/words/{word_id}", headers=auth_headers)
    assert still_there.status_code == 200
    assert still_there.json()["term"] == "collaborate"

    # And they are off the roster.
    roster = await client.get(f"/api/v1/decks/{deck_id}/roster", headers=auth_headers)
    assert len(roster.json()["members"]) == 1


async def test_deleting_a_member_does_not_touch_other_members_progress(
    client: AsyncClient,
    auth_headers: dict[str, str],
    user: UserModel,
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    deck_id = await create_deck(client, auth_headers, "Class 5B")
    word_id = await add_word(client, auth_headers, deck_id, "improve")
    student = await make_user(phone="+989121110082")
    await join(session_factory, deck_id, student.id)

    for headers in (auth_headers, bearer(student.id)):
        await client.post(
            f"/api/v1/study/words/{word_id}/grade", headers=headers, json={"grade": "good"}
        )

    assert (await client.delete("/api/v1/users/me", headers=bearer(student.id))).status_code == 204

    async with session_factory() as session:
        assert await session.get(WordProgressModel, (student.id, UUID(word_id))) is None
        mine = await session.get(WordProgressModel, (user.id, UUID(word_id)))
        assert mine is not None and mine.box == 2


async def test_deletion_needs_authentication(client: AsyncClient) -> None:
    assert (await client.delete("/api/v1/users/me")).status_code == 401
