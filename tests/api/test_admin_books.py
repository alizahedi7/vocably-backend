"""The admin's side of books: queue an ingest, review, publish.

Every route takes ``CurrentAdmin`` — the rule CLAUDE.md states for the whole
surface — so a learner is refused with 403 and an anonymous caller with 401.
Ingest is queued and never run in a request; uploads are an operator command,
because a file path on the API host is not something a dashboard should name.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.services.book_ingest_service import materialise
from app.domain.entities.book import Book
from app.domain.enums import BookSource
from app.infrastructure.books.epub import parse_epub
from app.infrastructure.books.sources import FetchedBook
from app.infrastructure.db.repositories.book_repository import SqlAlchemyBookRepository
from tests.epub_builder import build_epub, standard_ebooks_book

from .conftest import UserFactory, bearer

Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture
async def admin_headers(make_user: UserFactory) -> dict[str, str]:
    admin = await make_user(phone="+989120000009", is_admin=True)
    return bearer(admin.id)


async def seed(sessions: Sessions, *, rights: str = "", source_id: str = "up-1") -> Book:
    book = materialise(
        parse_epub(build_epub(standard_ebooks_book(chapters=2))),
        FetchedBook(
            source=BookSource.UPLOAD, source_id=source_id, source_url="", data=b"", rights=rights
        ),
    )
    # The EPUB itself states rights; an upload that says nothing is the case
    # publishing must stop, so drop the file's line too.
    book.rights = rights
    async with sessions() as session:
        stored = await SqlAlchemyBookRepository(session).create(book)
        await session.commit()
    return stored


async def test_every_book_route_is_admin_only(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    for method, url in [
        ("GET", "/api/v1/admin/books"),
        ("GET", "/api/v1/admin/books/00000000-0000-0000-0000-000000000000"),
        ("PATCH", "/api/v1/admin/books/00000000-0000-0000-0000-000000000000/publish"),
        ("POST", "/api/v1/admin/books/ingest"),
    ]:
        body: dict[str, Any] = {"source": "gutenberg", "ref": "11"} if method == "POST" else {}
        learner = await client.request(method, url, headers=auth_headers, json=body)
        anonymous = await client.request(method, url, json=body)
        assert (learner.status_code, anonymous.status_code) == (403, 401), url


async def test_an_ingest_is_queued_not_run(
    client: AsyncClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.tasks import books

    queued: list[tuple[str, ...]] = []

    class Result:
        id = "task-1"

    def record(*args: str) -> Result:
        queued.append(args)
        return Result()

    monkeypatch.setattr(books.ingest_book, "delay", record)
    response = await client.post(
        "/api/v1/admin/books/ingest",
        headers=admin_headers,
        json={"source": "standard_ebooks", "ref": " https://standardebooks.org/ebooks/a/b "},
    )
    assert response.status_code == 202
    assert response.json() == {"taskId": "task-1"}
    assert queued == [("standard_ebooks", "https://standardebooks.org/ebooks/a/b")]


async def test_uploads_are_not_an_api_call(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/admin/books/ingest",
        headers=admin_headers,
        json={"source": "upload", "ref": "/etc/passwd"},
    )
    assert response.status_code == 422


async def test_admins_see_private_books_with_their_chapters(
    client: AsyncClient, admin_headers: dict[str, str], session_factory: Sessions
) -> None:
    book = await seed(session_factory)
    listing = (await client.get("/api/v1/admin/books", headers=admin_headers)).json()
    assert listing["total"] == 1
    assert listing["items"][0]["isPublic"] is False
    detail = (await client.get(f"/api/v1/admin/books/{book.id}", headers=admin_headers)).json()
    assert [c["title"] for c in detail["chapters"]] == ["I: Title 1", "II: Title 2"]
    assert detail["chapters"][0]["wordCount"] > 0


@pytest.fixture
def warm_queue(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Records the books queued for pre-warming instead of reaching a broker."""
    from app.tasks import books

    queued: list[str] = []

    class Result:
        id = "warm-1"

    def record(book_id: str) -> Result:
        queued.append(book_id)
        return Result()

    monkeypatch.setattr(books.warm_book, "delay", record)
    return queued


async def test_publishing_needs_a_rights_statement_and_goes_both_ways(
    client: AsyncClient,
    admin_headers: dict[str, str],
    auth_headers: dict[str, str],
    session_factory: Sessions,
    warm_queue: list[str],
) -> None:
    book = await seed(session_factory)
    url = f"/api/v1/admin/books/{book.id}/publish"
    refused = await client.patch(url, headers=admin_headers, json={"is_public": True})
    assert refused.status_code == 422
    assert "rights" in refused.json()["detail"]

    published = await client.patch(
        url, headers=admin_headers, json={"is_public": True, "rights": "Public domain in the USA."}
    )
    assert published.status_code == 200
    assert published.json()["isPublic"] is True
    assert published.json()["rights"] == "Public domain in the USA."
    library = (await client.get("/api/v1/books", headers=auth_headers)).json()
    assert library["total"] == 1

    hidden = await client.patch(url, headers=admin_headers, json={"is_public": False})
    assert hidden.json()["isPublic"] is False and hidden.json()["publishedAt"] is None
    assert (await client.get("/api/v1/books", headers=auth_headers)).json()["total"] == 0
    # Publishing queued the book's vocabulary for pre-warming; unpublishing did not.
    assert warm_queue == [str(book.id)]


async def test_an_unknown_book_is_a_404(client: AsyncClient, admin_headers: dict[str, str]) -> None:
    url = "/api/v1/admin/books/00000000-0000-0000-0000-000000000000"
    assert (await client.get(url, headers=admin_headers)).status_code == 404
    assert (
        await client.patch(f"{url}/publish", headers=admin_headers, json={"is_public": True})
    ).status_code == 404
