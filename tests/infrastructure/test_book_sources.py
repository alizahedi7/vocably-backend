"""Fetching books: the two catalogues' real quirks, with no network.

Each case is one measured behaviour of a real source. Standard Ebooks answers
its plain download URL with an HTML page and status 200, so the fetcher both
appends ``?source=download`` and refuses anything that is not a zip. Gutendex
flags books that are not public domain, and those are refused.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.core.exceptions import ExternalServiceError, ValidationError
from app.domain.enums import BookSource
from app.infrastructure.books.sources import BookFetcher
from tests.epub_builder import build_epub, standard_ebooks_book

EPUB = build_epub(standard_ebooks_book())

Handler = Callable[[httpx.Request], httpx.Response]


def fetcher(handler: Handler) -> tuple[BookFetcher, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return BookFetcher(client), seen


def gutendex(meta: dict[str, object], epub: bytes = EPUB) -> Handler:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "gutendex.com":
            return httpx.Response(200, json=meta)
        return httpx.Response(200, content=epub)

    return handle


# ── Project Gutenberg ─────────────────────────────────────────


async def test_a_gutenberg_book_is_fetched_by_number_with_its_catalogue_facts() -> None:
    meta = {
        "copyright": False,
        "subjects": ["Fiction"],
        "download_count": 9,
        "formats": {
            "application/epub+zip": "https://www.gutenberg.org/ebooks/11.epub3.images",
            "image/jpeg": "https://www.gutenberg.org/cache/epub/11/pg11.cover.medium.jpg",
        },
    }
    books, seen = fetcher(gutendex(meta))
    fetched = await books.fetch(
        source=BookSource.GUTENBERG, ref="https://www.gutenberg.org/ebooks/11"
    )
    assert fetched.source_id == "11"
    assert fetched.data == EPUB
    assert fetched.cover_url.endswith("pg11.cover.medium.jpg")
    assert fetched.extra["subjects"] == ["Fiction"]
    assert "Public domain" in fetched.rights
    assert [r.url.host for r in seen] == ["gutendex.com", "www.gutenberg.org"]
    # Cloudflare in front of these sites rejects default client user agents.
    assert all("VocablyBot" in r.headers["user-agent"] for r in seen)


async def test_a_book_the_catalogue_marks_copyrighted_is_refused() -> None:
    books, seen = fetcher(gutendex({"copyright": True, "formats": {}}))
    with pytest.raises(ValidationError, match="not marked public domain"):
        await books.fetch(source=BookSource.GUTENBERG, ref="1342")
    assert len(seen) == 1  # refused before downloading anything


async def test_a_gutenberg_reference_must_contain_a_number() -> None:
    books, _ = fetcher(gutendex({}))
    with pytest.raises(ValidationError):
        await books.fetch(source=BookSource.GUTENBERG, ref="pride and prejudice")


async def test_an_unreachable_catalogue_is_an_external_failure() -> None:
    books, _ = fetcher(lambda request: httpx.Response(503))
    with pytest.raises(ExternalServiceError):
        await books.fetch(source=BookSource.GUTENBERG, ref="11")


# ── Standard Ebooks ───────────────────────────────────────────


async def test_a_standard_ebooks_page_url_becomes_its_real_download() -> None:
    books, seen = fetcher(lambda request: httpx.Response(200, content=EPUB))
    page = "https://standardebooks.org/ebooks/jane-austen/pride-and-prejudice"
    fetched = await books.fetch(source=BookSource.STANDARD_EBOOKS, ref=page + "/")
    assert str(seen[0].url) == (
        page + "/downloads/jane-austen_pride-and-prejudice.epub?source=download"
    )
    assert fetched.source_id == page
    assert "CC0" in fetched.rights


async def test_an_html_page_served_with_200_is_not_mistaken_for_an_ebook() -> None:
    interstitial = b"<!DOCTYPE html><title>Your Download Has Started!</title>"
    books, _ = fetcher(lambda request: httpx.Response(200, content=interstitial))
    with pytest.raises(ExternalServiceError, match="not an ebook"):
        await books.fetch(
            source=BookSource.STANDARD_EBOOKS,
            ref="https://standardebooks.org/ebooks/jane-austen/pride-and-prejudice",
        )


@pytest.mark.parametrize(
    "ref",
    [
        "https://example.com/ebooks/jane-austen/pride-and-prejudice",
        "https://standardebooks.org/ebooks/jane-austen",
        "https://standardebooks.org/about",
    ],
)
async def test_anything_but_a_standard_ebooks_book_page_is_refused(ref: str) -> None:
    books, seen = fetcher(lambda request: httpx.Response(200, content=EPUB))
    with pytest.raises(ValidationError):
        await books.fetch(source=BookSource.STANDARD_EBOOKS, ref=ref)
    assert seen == []


# ── Uploads ───────────────────────────────────────────────────


async def test_an_upload_is_identified_by_its_hash_and_states_no_rights(tmp_path: Path) -> None:
    path = tmp_path / "book.epub"
    path.write_bytes(EPUB)
    books, seen = fetcher(lambda request: httpx.Response(500))
    fetched = await books.fetch(source=BookSource.UPLOAD, ref=str(path))
    again = await books.fetch(source=BookSource.UPLOAD, ref=str(path))
    assert fetched.source_id == again.source_id and len(fetched.source_id) == 64
    assert fetched.rights == ""
    assert seen == []
