"""``GET /words?box=N`` — one Leitner box, rather than the whole library.

The box roster in the app lists the words at one stage. Without this it had to
fetch every card the learner owns and filter on the device, which for somebody
holding "1100 Words You Need to Know" is eleven hundred rows to show one box.

Two things make the filter more than a convenience, and both are asserted
below: it is computed in SQL over the same ``COALESCE(box, 1)`` every other read
uses, so it cannot disagree with the memory-strength tally; and it **implies
started**, because a card nobody has started is in no box at all.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.models.deck import DeckModel
from tests.api.conftest import UserFactory, bearer, sleep_on_it


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


async def move(client: AsyncClient, headers: dict[str, str], word_id: str, box: int) -> None:
    response = await client.post(
        f"/api/v1/study/words/{word_id}/box", headers=headers, json={"box": box}
    )
    assert response.status_code == 200, response.text


async def terms_in_box(
    client: AsyncClient, headers: dict[str, str], box: int, *, deck_id: str | None = None
) -> list[str]:
    query = f"box={box}" + (f"&deck_id={deck_id}" if deck_id else "")
    response = await client.get(f"/api/v1/words?{query}", headers=headers)
    assert response.status_code == 200, response.text
    return sorted(w["term"] for w in response.json())


async def test_a_box_answers_only_the_cards_in_it(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    deck = await create_deck(client, auth_headers)
    mastered = await add_word(client, auth_headers, deck, "undermine")
    familiar = await add_word(client, auth_headers, deck, "keen")
    await add_word(client, auth_headers, deck, "oath")
    await move(client, auth_headers, mastered, 5)
    await move(client, auth_headers, familiar, 3)

    assert await terms_in_box(client, auth_headers, 5) == ["undermine"]
    assert await terms_in_box(client, auth_headers, 3) == ["keen"]
    # Never studied, and in an ordinary deck: box 1 is what that means.
    assert await terms_in_box(client, auth_headers, 1) == ["oath"]
    assert await terms_in_box(client, auth_headers, 2) == []


async def test_it_agrees_with_the_memory_strength_tally(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The filter and the home screen's five figures are one definition of "box".

    They are computed by different queries, so this is the test that says the
    ``COALESCE(box, 1)`` behind each of them is the same expression.
    """
    deck = await create_deck(client, auth_headers)
    for term, box in [("a", 5), ("b", 5), ("c", 3), ("d", 1), ("e", 4)]:
        word = await add_word(client, auth_headers, deck, term)
        if box != 1:
            await move(client, auth_headers, word, box)
    await sleep_on_it(session_factory)

    overview = (await client.get("/api/v1/study/overview", headers=auth_headers)).json()
    counts = {b["box"]: b["count"] for b in overview["memory_strength"]["distribution"]}

    for box in (1, 2, 3, 4, 5):
        listed = await terms_in_box(client, auth_headers, box)
        assert len(listed) == counts[box], f"box {box}: {listed} vs tally {counts[box]}"


async def test_a_box_never_answers_a_card_nobody_has_started(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The reason ``box`` implies started.

    A self-paced deck's untouched cards read as box 1 the way a blank form reads
    as its default. Answering ``box=1`` with them would hand the roster a whole
    504-word import the learner has never met — exactly what self-pacing exists
    to prevent.
    """
    author = await make_user(phone="+989120000101")
    source = await create_deck(client, bearer(author.id), "504 Essential Words")
    for term in ("alpha", "beta", "gamma"):
        await add_word(client, bearer(author.id), source, term)
    async with session_factory() as session:
        await session.execute(
            update(DeckModel)
            .where(DeckModel.id == UUID(source))
            .values(
                is_public=True,
                category="general",
                description="A deck",
                description_fa="یک دسته",
                published_at=datetime.now(UTC),
            )
        )
        await session.commit()
    imported = await client.post(f"/api/v1/decks/public/{source}/import", headers=auth_headers)
    copy_id = imported.json()["id"]

    # The deck itself still lists in full — the screen says "3 words".
    everything = (await client.get(f"/api/v1/words?deck_id={copy_id}", headers=auth_headers)).json()
    assert len(everything) == 3
    assert [w["started"] for w in everything] == [False, False, False]

    assert await terms_in_box(client, auth_headers, 1) == []

    # Start one, and only that one appears.
    started = await client.post(
        f"/api/v1/decks/{copy_id}/start",
        headers=auth_headers,
        json={"word_ids": [everything[0]["id"]]},
    )
    assert started.status_code in (200, 201), started.text
    assert await terms_in_box(client, auth_headers, 1) == [everything[0]["term"]]


async def test_box_composes_with_deck(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    everyday = await create_deck(client, auth_headers, "Everyday")
    idioms = await create_deck(client, auth_headers, "Idioms")
    here = await add_word(client, auth_headers, everyday, "undermine")
    there = await add_word(client, auth_headers, idioms, "run out")
    await move(client, auth_headers, here, 5)
    await move(client, auth_headers, there, 5)

    assert await terms_in_box(client, auth_headers, 5) == ["run out", "undermine"]
    assert await terms_in_box(client, auth_headers, 5, deck_id=everyday) == ["undermine"]


async def test_a_box_spans_every_deck_the_learner_belongs_to(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    """A library-wide roster is the point: a stage is a fact about the learner,
    not about one deck."""
    teacher = await make_user(phone="+989120000102", username="teacher")
    student = await make_user(phone="+989120000103", username="student")
    mine = await create_deck(client, bearer(student.id), "Mine")
    ours = await create_deck(client, bearer(teacher.id), "Class deck")
    own = await add_word(client, bearer(student.id), mine, "undermine")
    shared = await add_word(client, bearer(teacher.id), ours, "reluctant")
    added = await client.post(
        f"/api/v1/decks/{ours}/members",
        headers=bearer(teacher.id),
        json={"username": "student", "role": "viewer"},
    )
    assert added.status_code in (200, 201), added.text
    await client.post(
        f"/api/v1/decks/{ours}/start", headers=bearer(student.id), json={"word_ids": [shared]}
    )
    await move(client, bearer(student.id), own, 5)
    await move(client, bearer(student.id), shared, 5)

    assert await terms_in_box(client, bearer(student.id), 5) == ["reluctant", "undermine"]
    # And it is *their* box: the teacher's own progress on the shared card is
    # untouched, because two members hold two rows against one words.id.
    assert await terms_in_box(client, bearer(teacher.id), 5) == []


async def test_an_invalid_box_is_rejected(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    for box in (0, 6, 99):
        response = await client.get(f"/api/v1/words?box={box}", headers=auth_headers)
        assert response.status_code == 422, f"box={box}: {response.text}"


async def test_omitting_the_box_still_lists_everything(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Additive, so an Android build that predates the parameter is unaffected —
    and it outlives the deploy by weeks."""
    deck = await create_deck(client, auth_headers)
    mastered = await add_word(client, auth_headers, deck, "undermine")
    await add_word(client, auth_headers, deck, "keen")
    await move(client, auth_headers, mastered, 5)

    listed = (await client.get("/api/v1/words", headers=auth_headers)).json()
    assert sorted(w["term"] for w in listed) == ["keen", "undermine"]
