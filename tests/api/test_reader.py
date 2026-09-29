"""The library and the reader, over HTTP, against the real app.

The contracts pinned here are the ones a client builds on: a private book is a
404 to learners; a tap goes through the flashcard lookup chain with the lemma,
so it shares the cache, the lexicon and the ``lookup_id`` with ``/ai/lookup``;
the sentence is read from the book itself when the tap says where it was; a
paragraph translation is shared only when the text is a book's; and a reading
position's percentage is computed here, never accepted.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_ai_provider
from app.application.ports.ai_service import (
    AIService,
    GeneratedStory,
    LearnerContext,
    LookupResult,
    MeaningSuggestion,
)
from app.application.services.book_ingest_service import materialise
from app.core.config import settings
from app.domain.entities.book import Book
from app.domain.enums import BookSource
from app.infrastructure.books.epub import parse_epub
from app.infrastructure.books.sources import FetchedBook
from app.infrastructure.db.repositories.book_repository import SqlAlchemyBookRepository
from app.main import app
from tests.epub_builder import Book as EpubBook
from tests.epub_builder import Doc, build_epub, prose

from .conftest import UserFactory, bearer

Sessions = async_sessionmaker[AsyncSession]

MONEY = "She paid her salary cheque into the banks to keep the money safe."
BANK = [
    MeaningSuggestion(
        "بانک",
        "an organization that keeps and lends money",
        "I paid the cheque into the bank.",
        "Finance",
        "noun",
    ),
    MeaningSuggestion(
        "ساحل",
        "the land along the side of a river",
        "They sat on the river bank fishing.",
        "River",
        "noun",
    ),
]


class CountingProvider(AIService):
    """The bottom of the lookup chain; counts what actually reaches a model."""

    def __init__(self) -> None:
        self.terms: list[str] = []

    async def look_up_meanings(self, term: str, learner: LearnerContext) -> LookupResult:
        self.terms.append(term)
        if term == "bank":
            return LookupResult(term="bank", suggestions=list(BANK))
        return LookupResult(
            term=term,
            suggestions=[MeaningSuggestion("معنی", "a sense", "An example.", "General", "noun")],
        )

    async def generate_story(self, words: list[str], learner: LearnerContext) -> GeneratedStory:
        return GeneratedStory(text="", words_used=words)


@pytest.fixture
async def provider() -> AsyncGenerator[CountingProvider, None]:
    counting = CountingProvider()
    app.dependency_overrides[get_ai_provider] = lambda: counting
    yield counting
    app.dependency_overrides.pop(get_ai_provider, None)


async def seed_book(sessions: Sessions, *, public: bool = True, source_id: str = "11") -> Book:
    body_one = f"<h2>I</h2><p>{MONEY} {prose(4)}</p><p>{prose(word='second')}</p>"
    body_two = f"<h2>II</h2><p>{prose(word='two')}</p><p>{prose(word='last')}</p>"
    epub = EpubBook(
        docs=[Doc("c1.xhtml", body_one, "chapter"), Doc("c2.xhtml", body_two, "chapter")]
    )
    book = materialise(
        parse_epub(build_epub(epub)),
        FetchedBook(
            source=BookSource.GUTENBERG,
            source_id=source_id,
            source_url="",
            data=b"",
            rights="Public domain in the USA.",
        ),
    )
    async with sessions() as session:
        repo = SqlAlchemyBookRepository(session)
        stored = await repo.create(book)
        if public:
            stored = await repo.set_published(stored.id, True) or stored
        await session.commit()
    return stored


async def first_block(client: AsyncClient, headers: dict[str, str], book: Book) -> dict[str, Any]:
    response = await client.get(
        f"/api/v1/books/{book.id}/chapters/{book.chapters[0].id}", headers=headers
    )
    assert response.status_code == 200, response.text
    block: dict[str, Any] = response.json()["blocks"][0]
    return block


# ── The library ───────────────────────────────────────────────


async def test_the_library_lists_only_published_books(
    client: AsyncClient, auth_headers: dict[str, str], session_factory: Sessions
) -> None:
    await seed_book(session_factory, source_id="1")
    await seed_book(session_factory, public=False, source_id="2")
    response = await client.get("/api/v1/books", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "A Test Book"
    assert body["items"][0]["total_chapters"] == 2


async def test_the_library_needs_a_token(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/books")).status_code == 401


async def test_opening_a_book_returns_its_contents_and_no_position_yet(
    client: AsyncClient, auth_headers: dict[str, str], session_factory: Sessions
) -> None:
    book = await seed_book(session_factory)
    body = (await client.get(f"/api/v1/books/{book.id}", headers=auth_headers)).json()
    assert [c["title"] for c in body["chapters"]] == ["I", "II"]
    assert body["progress"] is None
    assert body["rights"] == "Public domain in the USA."


async def test_a_private_book_is_a_404_to_learners_and_readable_by_admins(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: Sessions,
    make_user: UserFactory,
) -> None:
    book = await seed_book(session_factory, public=False)
    admin = await make_user(phone="+989120000001", is_admin=True)
    assert (await client.get(f"/api/v1/books/{book.id}", headers=auth_headers)).status_code == 404
    preview = await client.get(f"/api/v1/books/{book.id}", headers=bearer(admin.id))
    assert preview.status_code == 200


async def test_a_chapter_pages_by_block_position(
    client: AsyncClient, auth_headers: dict[str, str], session_factory: Sessions
) -> None:
    book = await seed_book(session_factory)
    url = f"/api/v1/books/{book.id}/chapters/{book.chapters[0].id}"
    first = (await client.get(url, headers=auth_headers, params={"limit": 1})).json()
    assert [b["position"] for b in first["blocks"]] == [0]
    assert first["next_after"] == 0
    rest = (
        await client.get(url, headers=auth_headers, params={"after": first["next_after"]})
    ).json()
    assert [b["position"] for b in rest["blocks"]] == [1]
    assert rest["next_after"] is None


async def test_a_chapter_is_not_readable_through_another_book(
    client: AsyncClient, auth_headers: dict[str, str], session_factory: Sessions
) -> None:
    one = await seed_book(session_factory, source_id="1")
    two = await seed_book(session_factory, source_id="2")
    response = await client.get(
        f"/api/v1/books/{two.id}/chapters/{one.chapters[0].id}", headers=auth_headers
    )
    assert response.status_code == 404


# ── Tapping a word ────────────────────────────────────────────


async def test_a_tap_looks_up_the_lemma_and_marks_the_sense_its_sentence_uses(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: Sessions,
    provider: CountingProvider,
) -> None:
    book = await seed_book(session_factory)
    block = await first_block(client, auth_headers, book)
    start = block["text"].index("banks")
    response = await client.post(
        "/api/v1/reader/lookup-word",
        headers=auth_headers,
        json={
            "word": "banks",
            "block_id": block["id"],
            "char_start": start,
            "char_end": start + 5,
            "book_id": str(book.id),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["surface"], body["lemma"]) == ("banks", "bank")
    assert provider.terms == ["bank"]  # the lemma, never the sentence
    assert body["lookup"]["term"] == "bank"
    assert len(body["lookup"]["suggestions"]) == 2
    # "salary cheque … money" is the finance sense, decided without a model.
    assert (body["contextual_index"], body["selection"]) == (0, "overlap")


async def test_a_tap_shares_the_flashcard_cache_and_lookup_id(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: Sessions,
    provider: CountingProvider,
) -> None:
    await seed_book(session_factory)
    tapped = await client.post(
        "/api/v1/reader/lookup-word", headers=auth_headers, json={"word": "bank"}
    )
    typed = await client.post("/api/v1/ai/lookup", headers=auth_headers, json={"term": "bank"})
    assert tapped.status_code == typed.status_code == 200
    assert tapped.json()["lookup"]["lookup_id"] == typed.json()["lookup_id"] != ""
    assert provider.terms == ["bank"]  # the flashcard lookup cost nothing


async def test_with_no_sentence_the_most_common_sense_is_shown(
    client: AsyncClient, auth_headers: dict[str, str], provider: CountingProvider
) -> None:
    body = (
        await client.post("/api/v1/reader/lookup-word", headers=auth_headers, json={"word": "bank"})
    ).json()
    assert (body["contextual_index"], body["selection"]) == (0, "first")


async def test_offsets_that_do_not_fit_the_block_fall_back_to_the_sentence_sent(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: Sessions,
    provider: CountingProvider,
) -> None:
    book = await seed_book(session_factory)
    block = await first_block(client, auth_headers, book)
    response = await client.post(
        "/api/v1/reader/lookup-word",
        headers=auth_headers,
        json={
            "word": "bank",
            "block_id": block["id"],
            "char_start": 99_999,
            "char_end": 100_004,
            "sentence_context": "They sat on the river bank fishing.",
        },
    )
    assert response.status_code == 200
    assert response.json()["contextual_index"] == 1


async def test_a_sentence_is_not_a_tap(
    client: AsyncClient, auth_headers: dict[str, str], provider: CountingProvider
) -> None:
    response = await client.post(
        "/api/v1/reader/lookup-word",
        headers=auth_headers,
        json={"word": "this is a whole sentence that somebody selected " * 2},
    )
    assert response.status_code == 422
    assert provider.terms == []


async def test_taps_are_rate_limited_per_user(
    client: AsyncClient,
    auth_headers: dict[str, str],
    provider: CountingProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "reader_lookups_per_user_per_hour", 1)
    url = "/api/v1/reader/lookup-word"
    assert (await client.post(url, headers=auth_headers, json={"word": "bank"})).status_code == 200
    blocked = await client.post(url, headers=auth_headers, json={"word": "bank"})
    assert blocked.status_code == 429
    assert blocked.json()["detail"]


# ── Translating a paragraph ───────────────────────────────────


async def test_a_books_paragraph_is_translated_once_for_everyone(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: Sessions,
    make_user: UserFactory,
) -> None:
    book = await seed_book(session_factory)
    block = await first_block(client, auth_headers, book)
    url = "/api/v1/reader/translate-paragraph"
    first = (await client.post(url, headers=auth_headers, json={"block_id": block["id"]})).json()
    other = await make_user(phone="+989120000002", native_language="Persian")
    again = (
        await client.post(
            url,
            headers=bearer(other.id),
            json={"block_id": block["id"], "target_language": "English"},
        )
    ).json()
    assert first["cached"] is False and first["translation"].startswith("[English]")
    assert again["cached"] is True and again["translation"] == first["translation"]


async def test_book_text_sent_as_free_text_still_hits_the_shared_cache(
    client: AsyncClient, auth_headers: dict[str, str], session_factory: Sessions
) -> None:
    book = await seed_book(session_factory)
    block = await first_block(client, auth_headers, book)
    url = "/api/v1/reader/translate-paragraph"
    await client.post(url, headers=auth_headers, json={"block_id": block["id"]})
    mangled = "  " + block["text"].replace(" ", "   ") + "\n"
    response = await client.post(url, headers=auth_headers, json={"paragraph_text": mangled})
    assert response.json()["cached"] is True


async def test_a_learners_own_text_is_never_stored(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    url = "/api/v1/reader/translate-paragraph"
    payload = {"paragraph_text": "My private diary entry about my day."}
    first = (await client.post(url, headers=auth_headers, json=payload)).json()
    again = (await client.post(url, headers=auth_headers, json=payload)).json()
    assert first["cached"] is False and again["cached"] is False


async def test_a_private_books_paragraph_is_a_404(
    client: AsyncClient, auth_headers: dict[str, str], session_factory: Sessions
) -> None:
    book = await seed_book(session_factory, public=False)
    async with session_factory() as session:
        chapter = await SqlAlchemyBookRepository(session).get_chapter(book.id, book.chapters[0].id)
    assert chapter is not None
    response = await client.post(
        "/api/v1/reader/translate-paragraph",
        headers=auth_headers,
        json={"block_id": str(chapter.blocks[0].id)},
    )
    assert response.status_code == 404


@pytest.mark.parametrize("payload", [{}, {"paragraph_text": "   "}, {"block_id": None}])
async def test_there_must_be_something_to_translate(
    client: AsyncClient, auth_headers: dict[str, str], payload: dict[str, Any]
) -> None:
    response = await client.post(
        "/api/v1/reader/translate-paragraph", headers=auth_headers, json=payload
    )
    assert response.status_code == 422


# ── Reading position ──────────────────────────────────────────


async def test_a_position_is_stored_with_a_percentage_computed_here(
    client: AsyncClient, auth_headers: dict[str, str], session_factory: Sessions
) -> None:
    book = await seed_book(session_factory)
    async with session_factory() as session:
        last = await SqlAlchemyBookRepository(session).get_chapter(book.id, book.chapters[1].id)
    assert last is not None
    block = last.blocks[-1]
    response = await client.post(
        "/api/v1/reader/progress",
        headers=auth_headers,
        json={"book_id": str(book.id), "block_id": str(block.id), "char_offset": 10**6},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["percent"] == 100  # the end of the last paragraph
    assert body["char_offset"] == len(block.text)  # clamped to the block
    opened = (await client.get(f"/api/v1/books/{book.id}", headers=auth_headers)).json()
    assert opened["progress"]["block_id"] == str(block.id)


async def test_the_shelf_lists_books_in_progress_and_forgets_on_request(
    client: AsyncClient, auth_headers: dict[str, str], session_factory: Sessions
) -> None:
    book = await seed_book(session_factory)
    block = await first_block(client, auth_headers, book)
    await client.post(
        "/api/v1/reader/progress",
        headers=auth_headers,
        json={"book_id": str(book.id), "block_id": block["id"]},
    )
    shelf = (await client.get("/api/v1/reader/progress", headers=auth_headers)).json()
    assert [p["book_id"] for p in shelf] == [str(book.id)]
    url = f"/api/v1/reader/progress/{book.id}"
    assert (await client.delete(url, headers=auth_headers)).status_code == 204
    assert (await client.delete(url, headers=auth_headers)).status_code == 404
    assert (await client.get("/api/v1/reader/progress", headers=auth_headers)).json() == []


async def test_a_position_must_be_in_the_book_it_claims(
    client: AsyncClient, auth_headers: dict[str, str], session_factory: Sessions
) -> None:
    one = await seed_book(session_factory, source_id="1")
    two = await seed_book(session_factory, source_id="2")
    block = await first_block(client, auth_headers, one)
    wrong_book = await client.post(
        "/api/v1/reader/progress",
        headers=auth_headers,
        json={"book_id": str(two.id), "block_id": block["id"]},
    )
    unknown = await client.post(
        "/api/v1/reader/progress",
        headers=auth_headers,
        json={"book_id": str(uuid4()), "block_id": block["id"]},
    )
    assert wrong_book.status_code == 422
    assert unknown.status_code == 404
