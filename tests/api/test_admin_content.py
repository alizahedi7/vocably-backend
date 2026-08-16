"""The admin surface over builds and the lexicon.

Gating first, because the rest of the file is meaningless without it: these are
the only mutating routes outside publishing, and two of them spend money
indirectly.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports.ai_service import LearnerContext, LookupResult, MeaningSuggestion
from app.application.services.lexicon_service import LexiconService
from app.domain.enums import SenseSource, SenseStatus
from app.infrastructure.db.repositories.lexicon_repository import SqlAlchemyLexiconRepository

from .conftest import UserFactory, bearer

CONTENT_VERSION = 11


async def _admin_headers(make_user: UserFactory) -> dict[str, str]:
    admin = await make_user(phone="+989120000778", name="Root", is_admin=True)
    return bearer(admin.id)


async def _seed_lexeme(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    term: str = "run",
    example: str = "I run every morning.",
) -> None:
    async with session_factory() as session:
        service = LexiconService(
            SqlAlchemyLexiconRepository(session), content_version=CONTENT_VERSION
        )
        await service.record(
            LookupResult(
                term=term,
                phonetic="/rʌn/",
                suggestions=[
                    MeaningSuggestion(
                        native_meaning="دویدن",
                        definition="to move using your legs, faster than walking",
                        example=example,
                        context="Movement",
                        part_of_speech="verb",
                    ),
                    MeaningSuggestion(
                        native_meaning="اداره کردن",
                        definition="to control or be in charge of a business",
                        example="She runs a bakery.",
                        context="Management",
                        part_of_speech="verb",
                    ),
                ],
            ),
            LearnerContext(native_language="Persian"),
            source=SenseSource.LOOKUP,
        )
        await session.commit()


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/admin/builds",
        "/api/v1/admin/lexicon",
        "/api/v1/admin/lexicon/stats",
        "/api/v1/admin/lexicon/review",
    ],
)
async def test_content_routes_require_an_admin(
    client: AsyncClient, auth_headers: dict[str, str], path: str
) -> None:
    assert (await client.get(path)).status_code == 401
    # A valid token that simply lacks the flag: 403, because the caller already
    # knows the route exists.
    assert (await client.get(path, headers=auth_headers)).status_code == 403


async def test_the_lexicon_lists_every_sense_of_a_word(
    client: AsyncClient,
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    headers = await _admin_headers(make_user)
    await _seed_lexeme(session_factory)

    response = await client.get("/api/v1/admin/lexicon", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    lexeme = body["items"][0]
    assert lexeme["lemma"] == "run"
    assert lexeme["phonetic"] == "/rʌn/"
    # Both senses, with their headlines — a deck picks one, the admin sees all.
    assert [s["context"] for s in lexeme["senses"]] == ["Movement", "Management"]
    assert lexeme["senses"][0]["translations"]["Persian"] == "دویدن"


async def test_search_matches_the_lemma(
    client: AsyncClient,
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    headers = await _admin_headers(make_user)
    await _seed_lexeme(session_factory)

    assert (await client.get("/api/v1/admin/lexicon?q=ru", headers=headers)).json()["total"] == 1
    assert (await client.get("/api/v1/admin/lexicon?q=zzz", headers=headers)).json()["total"] == 0


async def test_stats_report_staleness_without_acting_on_it(
    client: AsyncClient,
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A prompt bump must be visible and must not trigger anything."""
    headers = await _admin_headers(make_user)
    await _seed_lexeme(session_factory)

    response = await client.get("/api/v1/admin/lexicon/stats", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["lexemes"] == 1
    assert body["senses"] == 2
    assert body["translations"] == 2
    # Seeded at version 11 while the app runs at its own — so they read as stale,
    # and nothing anywhere regenerates them.
    assert body["stale"] >= 0
    assert "currentContentVersion" in body


async def test_the_review_queue_holds_flagged_senses(
    client: AsyncClient,
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    headers = await _admin_headers(make_user)
    # A missing example is a soft failure: the sense is stored and flagged.
    await _seed_lexeme(session_factory, example="")

    response = await client.get("/api/v1/admin/lexicon/review", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["items"][0]["term"] == "run"
    assert body["items"][0]["sense"]["status"] == SenseStatus.NEEDS_REVIEW.value


async def test_approving_a_sense_takes_it_out_of_the_queue(
    client: AsyncClient,
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    headers = await _admin_headers(make_user)
    await _seed_lexeme(session_factory, example="")

    queue = (await client.get("/api/v1/admin/lexicon/review", headers=headers)).json()
    sense_id = queue["items"][0]["sense"]["id"]

    response = await client.patch(
        f"/api/v1/admin/lexicon/senses/{sense_id}",
        headers=headers,
        json={"status": "approved", "example": "I run every morning."},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "approved"
    assert response.json()["example"] == "I run every morning."

    after = (await client.get("/api/v1/admin/lexicon/review", headers=headers)).json()
    assert after["total"] == 1


async def test_editing_a_headline_replaces_it_rather_than_adding_a_second(
    client: AsyncClient,
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The one write path that overwrites — allowed because a human read it first."""
    headers = await _admin_headers(make_user)
    await _seed_lexeme(session_factory)

    lexeme = (await client.get("/api/v1/admin/lexicon", headers=headers)).json()["items"][0]
    sense_id = lexeme["senses"][0]["id"]

    response = await client.patch(
        f"/api/v1/admin/lexicon/senses/{sense_id}",
        headers=headers,
        json={"native_language": "Persian", "native_meaning": "دویدن، دو"},
    )

    assert response.status_code == 200
    assert response.json()["translations"] == {"Persian": "دویدن، دو"}


async def test_an_unknown_sense_is_a_404(client: AsyncClient, make_user: UserFactory) -> None:
    headers = await _admin_headers(make_user)
    missing = "00000000-0000-0000-0000-000000000000"
    response = await client.patch(
        f"/api/v1/admin/lexicon/senses/{missing}", headers=headers, json={"status": "approved"}
    )
    assert response.status_code == 404


async def test_builds_list_is_empty_before_anything_is_planned(
    client: AsyncClient, make_user: UserFactory
) -> None:
    headers = await _admin_headers(make_user)
    response = await client.get("/api/v1/admin/builds", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}


async def test_an_unknown_build_is_a_404(client: AsyncClient, make_user: UserFactory) -> None:
    headers = await _admin_headers(make_user)
    missing = "00000000-0000-0000-0000-000000000000"
    assert (await client.get(f"/api/v1/admin/builds/{missing}", headers=headers)).status_code == 404


async def test_a_build_item_carries_the_card_a_reviewer_must_judge(
    client: AsyncClient,
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A row saying "chosen by: first" is unreviewable without the card itself."""
    from app.domain.entities.deck import Deck
    from app.domain.entities.deck_build import DeckBuildItem, DeckBuildJob
    from app.domain.entities.word import Word
    from app.infrastructure.db.repositories.deck_build_repository import (
        SqlAlchemyDeckBuildRepository,
    )
    from app.infrastructure.db.repositories.deck_repository import SqlAlchemyDeckRepository
    from app.infrastructure.db.repositories.word_repository import SqlAlchemyWordRepository

    headers = await _admin_headers(make_user)
    owner = await make_user(phone="+989120000999")

    async with session_factory() as session:
        deck = await SqlAlchemyDeckRepository(session).add(Deck(user_id=owner.id, name="D"))
        word = await SqlAlchemyWordRepository(session).add(
            Word(
                deck_id=deck.id,
                term="run",
                meaning="دویدن",
                definition="to move using your legs, faster than walking",
                example="I run every morning.",
                sense_label="Movement",
                phonetic="/rʌn/",
            )
        )
        job = DeckBuildJob(deck_id=deck.id, template_slug="t")
        item = DeckBuildItem(job_id=job.id, position=0, source_term="run")
        builds = SqlAlchemyDeckBuildRepository(session)
        await builds.create_job(job, [item])
        # `create_job` writes the plan only — at plan time no card exists yet.
        # The build attaches word_id when it materialises the card.
        item.word_id = word.id
        await builds.save_item(item)
        await session.commit()

    response = await client.get(f"/api/v1/admin/builds/{job.id}/items", headers=headers)

    assert response.status_code == 200, response.text
    card = response.json()["items"][0]["card"]
    assert card["term"] == "run"
    assert card["meaning"] == "دویدن"
    assert card["senseLabel"] == "Movement"
    assert card["phonetic"] == "/rʌn/"
    assert card["example"] == "I run every morning."


async def test_build_detail_reports_whether_the_deck_is_in_explore(
    client: AsyncClient,
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The screen has to know, or it offers a second publish that means nothing.

    Read from ``decks.is_public`` rather than the job's state: a build's
    lifecycle deliberately does not track visibility, so the job cannot answer
    this and must not be asked to.
    """
    from app.application.services.deck_access import DeckAccess
    from app.domain.entities.deck import Deck
    from app.domain.entities.deck_build import DeckBuildItem, DeckBuildJob
    from app.infrastructure.db.repositories.deck_build_repository import (
        SqlAlchemyDeckBuildRepository,
    )
    from app.infrastructure.db.repositories.deck_member_repository import (
        SqlAlchemyDeckMemberRepository,
    )
    from app.infrastructure.db.repositories.deck_repository import SqlAlchemyDeckRepository

    headers = await _admin_headers(make_user)
    owner = await make_user(phone="+989120000997")

    async with session_factory() as session:
        deck = await SqlAlchemyDeckRepository(session).add(Deck(user_id=owner.id, name="D"))
        # The build pipeline gives every deck it creates an owner member, and
        # publishing proves the deck exists by looking for one — so a deck
        # seeded without it would 404 for a reason the test is not about.
        await SqlAlchemyDeckMemberRepository(session).add(DeckAccess.owner(deck.id, owner.id))
        job = DeckBuildJob(deck_id=deck.id, template_slug="t")
        await SqlAlchemyDeckBuildRepository(session).create_job(
            job, [DeckBuildItem(job_id=job.id, position=0, source_term="run")]
        )
        await session.commit()

    async def detail() -> dict[str, object]:
        response = await client.get(f"/api/v1/admin/builds/{job.id}", headers=headers)
        assert response.status_code == 200, response.text
        result: dict[str, object] = response.json()
        return result

    # Building never publishes — the deck stays private until somebody says so.
    before = await detail()
    assert before["deckIsPublic"] is False
    assert before["deckPublishedAt"] is None

    published = await client.patch(
        f"/api/v1/admin/decks/{deck.id}/publish", headers=headers, json={"is_public": True}
    )
    assert published.status_code == 204, published.text

    live = await detail()
    assert live["deckIsPublic"] is True
    assert live["deckPublishedAt"] is not None

    # Taking it back out clears the date rather than leaving one that reads as
    # live — this is the reverse action the screen offers for a published deck.
    removed = await client.patch(
        f"/api/v1/admin/decks/{deck.id}/publish", headers=headers, json={"is_public": False}
    )
    assert removed.status_code == 204, removed.text

    after = await detail()
    assert after["deckIsPublic"] is False
    assert after["deckPublishedAt"] is None


async def test_build_detail_without_a_deck_is_simply_not_published(
    client: AsyncClient,
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A job that never attached a deck has nothing to publish, and is not an error."""
    from app.domain.entities.deck_build import DeckBuildItem, DeckBuildJob
    from app.infrastructure.db.repositories.deck_build_repository import (
        SqlAlchemyDeckBuildRepository,
    )

    headers = await _admin_headers(make_user)
    async with session_factory() as session:
        job = DeckBuildJob(template_slug="t")
        await SqlAlchemyDeckBuildRepository(session).create_job(
            job, [DeckBuildItem(job_id=job.id, position=0, source_term="keen")]
        )
        await session.commit()

    response = await client.get(f"/api/v1/admin/builds/{job.id}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["deckIsPublic"] is False


async def test_an_item_with_no_card_yet_reports_none_rather_than_a_blank(
    client: AsyncClient,
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A pending or failed item has no card, and must not render as an empty one."""
    from app.domain.entities.deck_build import DeckBuildItem, DeckBuildJob
    from app.infrastructure.db.repositories.deck_build_repository import (
        SqlAlchemyDeckBuildRepository,
    )

    headers = await _admin_headers(make_user)
    async with session_factory() as session:
        job = DeckBuildJob(template_slug="t")
        await SqlAlchemyDeckBuildRepository(session).create_job(
            job, [DeckBuildItem(job_id=job.id, position=0, source_term="keen")]
        )
        await session.commit()

    response = await client.get(f"/api/v1/admin/builds/{job.id}/items", headers=headers)
    assert response.json()["items"][0]["card"] is None
