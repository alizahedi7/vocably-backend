"""The point of the words/word_progress split: one deck, separate progress.

The sharing endpoints do not exist yet, so membership is inserted directly.
That is deliberate — these are the guarantees the *schema* has to make, and they
should be provable before any endpoint can hand out a membership row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import DeckRole
from app.infrastructure.db.models.deck_member import DeckMemberModel
from app.infrastructure.db.models.user import UserModel
from app.infrastructure.db.models.word import WordModel
from app.infrastructure.db.models.word_progress import WordProgressModel
from tests.api.conftest import UserFactory, bearer, sleep_on_it


async def create_deck(client: AsyncClient, headers: dict[str, str], name: str = "Class 5B") -> str:
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


async def test_two_members_hold_different_boxes_against_one_card(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    deck_id = await create_deck(client, auth_headers)
    word_id = await add_word(client, auth_headers, deck_id, "improve")
    student = await make_user(phone="+989121110001")
    await join(session_factory, deck_id, student.id)

    # The teacher gets it right twice; the student forgets it.
    for _ in range(2):
        graded = await client.post(
            f"/api/v1/study/words/{word_id}/grade", headers=auth_headers, json={"grade": "good"}
        )
        assert graded.status_code == 200, graded.text
    student_grade = await client.post(
        f"/api/v1/study/words/{word_id}/grade",
        headers=bearer(student.id),
        json={"grade": "again"},
    )
    assert student_grade.status_code == 200, student_grade.text

    # One card, two boxes. This is the whole reason the columns moved.
    teacher_view = await client.get(f"/api/v1/words/{word_id}", headers=auth_headers)
    student_view = await client.get(f"/api/v1/words/{word_id}", headers=bearer(student.id))
    assert teacher_view.json()["box"] == 3
    assert student_view.json()["box"] == 1
    assert teacher_view.json()["id"] == student_view.json()["id"]
    assert teacher_view.json()["term"] == student_view.json()["term"] == "improve"


async def test_a_word_nobody_has_studied_reads_as_new_and_due_the_next_day(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    deck_id = await create_deck(client, auth_headers)
    await add_word(client, auth_headers, deck_id, "reliable")
    student = await make_user(phone="+989121110002")
    await join(session_factory, deck_id, student.id)

    # No progress row was fanned out when the student joined...
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(WordProgressModel).where(WordProgressModel.user_id == student.id)
                )
            )
            .scalars()
            .all()
        )
    assert rows == []

    # ...and the word still reads correctly for them: box 1.
    listed = await client.get("/api/v1/words", headers=bearer(student.id))
    assert listed.status_code == 200
    assert [w["box"] for w in listed.json()] == [1]

    # A card added today is tomorrow's work, for the member as for anyone.
    today = await client.get("/api/v1/study/overview", headers=bearer(student.id))
    assert today.json()["due_count"] == 0
    await sleep_on_it(session_factory)

    overview = await client.get("/api/v1/study/overview", headers=bearer(student.id))
    assert overview.status_code == 200
    body = overview.json()
    assert body["due_count"] == 1
    assert body["total_count"] == 1
    assert body["memory_strength"]["distribution"][0] == {"box": 1, "label": "New", "count": 1}

    session_words = await client.get("/api/v1/study/session", headers=bearer(student.id))
    assert [w["term"] for w in session_words.json()["words"]] == ["reliable"]


async def test_deleting_a_member_leaves_the_cards_and_everyone_elses_progress(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    deck_id = await create_deck(client, auth_headers)
    word_id = await add_word(client, auth_headers, deck_id, "deadline")
    student = await make_user(phone="+989121110003")
    await join(session_factory, deck_id, student.id)

    for headers in (auth_headers, bearer(student.id)):
        await client.post(
            f"/api/v1/study/words/{word_id}/grade", headers=headers, json={"grade": "good"}
        )

    # A student leaving the school must not take the class's vocabulary with
    # them — the cascade from users lands on word_progress, not on words.
    async with session_factory() as session:
        await session.execute(delete(UserModel).where(UserModel.id == student.id))
        await session.commit()

    async with session_factory() as session:
        assert await session.get(WordModel, UUID(word_id)) is not None
        assert await session.get(WordProgressModel, (student.id, UUID(word_id))) is None

    still_there = await client.get(f"/api/v1/words/{word_id}", headers=auth_headers)
    assert still_there.status_code == 200
    assert still_there.json()["box"] == 2  # the teacher's own progress, untouched


async def test_a_viewer_may_study_but_not_edit(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    deck_id = await create_deck(client, auth_headers)
    word_id = await add_word(client, auth_headers, deck_id, "borrow")
    student = await make_user(phone="+989121110004")
    await join(session_factory, deck_id, student.id, DeckRole.VIEWER)
    headers = bearer(student.id)

    # Studying is not editing: a viewer grades like anyone else.
    assert (
        await client.post(
            f"/api/v1/study/words/{word_id}/grade", headers=headers, json={"grade": "good"}
        )
    ).status_code == 200

    # 403, not 404: they can see the deck, they just may not change it.
    for response in (
        await client.patch(f"/api/v1/words/{word_id}", headers=headers, json={"term": "lend"}),
        await client.delete(f"/api/v1/words/{word_id}", headers=headers),
        await client.post(
            "/api/v1/words",
            headers=headers,
            json={"deck_id": deck_id, "term": "sneak", "meaning": "in"},
        ),
        await client.delete(f"/api/v1/decks/{deck_id}", headers=headers),
    ):
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "permission_denied"


async def test_an_editor_adds_a_word_every_member_sees(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    deck_id = await create_deck(client, auth_headers)
    editor = await make_user(phone="+989121110005")
    viewer = await make_user(phone="+989121110006")
    await join(session_factory, deck_id, editor.id, DeckRole.EDITOR)
    await join(session_factory, deck_id, viewer.id, DeckRole.VIEWER)

    added = await client.post(
        "/api/v1/words",
        headers=bearer(editor.id),
        json={"deck_id": deck_id, "term": "collaborate", "meaning": "work together"},
    )
    assert added.status_code == 201, added.text

    # The same deck, not a copy: the card appears for everyone at once.
    for headers in (auth_headers, bearer(editor.id), bearer(viewer.id)):
        listed = await client.get("/api/v1/words", headers=headers, params={"deck_id": deck_id})
        assert [w["term"] for w in listed.json()] == ["collaborate"]


async def test_a_non_member_sees_nothing_and_is_told_nothing(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
) -> None:
    deck_id = await create_deck(client, auth_headers)
    word_id = await add_word(client, auth_headers, deck_id, "private")
    stranger = bearer((await make_user(phone="+989121110007")).id)

    # 404 everywhere: a stranger walking ids must not learn which ones exist.
    for response in (
        await client.get(f"/api/v1/decks/{deck_id}", headers=stranger),
        await client.get(f"/api/v1/words/{word_id}", headers=stranger),
        await client.patch(f"/api/v1/words/{word_id}", headers=stranger, json={"term": "x"}),
        await client.delete(f"/api/v1/words/{word_id}", headers=stranger),
        await client.post(
            f"/api/v1/study/words/{word_id}/grade", headers=stranger, json={"grade": "good"}
        ),
    ):
        assert response.status_code == 404, response.text

    assert (await client.get("/api/v1/decks", headers=stranger)).json() == []
    assert (await client.get("/api/v1/words", headers=stranger)).json() == []

    unauthenticated = await client.get(f"/api/v1/words/{word_id}")
    assert unauthenticated.status_code == 401
