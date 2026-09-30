"""Where a book's bytes come from. Fetching only — parsing is :mod:`epub`.

One adapter, one method per source, one result type, so the ingest service
never learns which catalogue it is talking to.

*Standard Ebooks* has no anonymous catalogue API — its OPDS feeds are for
Patrons Circle members — but every ebook page offers a direct EPUB download.
The ``/downloads/…epub`` URL answers with an HTML "your download has started"
page unless ``?source=download`` is appended; that was measured, not guessed.
So this adapter takes the ebook's page URL and derives the download from it:
the operator finds the book on the site and pastes the link.

*Project Gutenberg* is catalogued through Gutendex (an MIT-licensed JSON API
over the Gutenberg catalogue); the EPUB itself comes from gutenberg.org.
Gutenberg blocks aggressive fetchers, so this makes two requests per book and
never crawls.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, cast
from urllib.parse import urlparse

import httpx

from app.core.exceptions import ExternalServiceError, ValidationError
from app.core.logging import get_logger
from app.domain.enums import BookSource

logger = get_logger("vocably.books.sources")

_USER_AGENT: Final = "Mozilla/5.0 (compatible; VocablyBot/1.0; +https://vocably.ir)"
#: A novel is 1-25 MB (illustrated Gutenberg editions are the large ones).
#: Anything past this is not a text.
_MAX_BYTES: Final = 40 * 1024 * 1024
_GUTENDEX: Final = "https://gutendex.com/books/"
_GUTENBERG_EPUB: Final = "https://www.gutenberg.org/ebooks/{id}.epub3.images"
_SE_HOST: Final = "standardebooks.org"


@dataclass(slots=True)
class FetchedBook:
    source: BookSource
    source_id: str
    source_url: str
    data: bytes
    #: The source's own licence line, for ``books.rights``.
    rights: str = ""
    cover_url: str = ""
    #: Catalogue facts worth keeping but not querying. Lands in ``books.extra``.
    extra: dict[str, object] = field(default_factory=dict)


class BookFetcher:
    def __init__(self, client: httpx.AsyncClient, *, timeout_seconds: float = 60.0) -> None:
        self._client = client
        self._timeout = timeout_seconds

    async def fetch(self, *, source: BookSource, ref: str) -> FetchedBook:
        """Resolve ``ref`` — a number, a URL or a path — for ``source`` and download it."""
        if source is BookSource.GUTENBERG:
            return await self._gutenberg(ref)
        if source is BookSource.STANDARD_EBOOKS:
            return await self._standard_ebooks(ref)
        return self._upload(ref)

    # ── Project Gutenberg ─────────────────────────────────────

    async def _gutenberg(self, ref: str) -> FetchedBook:
        match = re.search(r"(\d+)", ref)
        if not match:
            raise ValidationError("A Gutenberg book is referenced by its number, e.g. 1342.")
        book_id = match.group(1)
        meta = await self._json(f"{_GUTENDEX}{book_id}/")
        if meta.get("copyright") is True:
            # Gutendex reports the catalogue's own flag. A book marked
            # copyrighted is distributed under a specific permission, which is
            # not what "public domain" means here.
            raise ValidationError(f"Gutenberg #{book_id} is not marked public domain.")
        formats = cast(dict[str, str], meta.get("formats") or {})
        epub_url = next(
            (url for mime, url in formats.items() if mime.startswith("application/epub")),
            _GUTENBERG_EPUB.format(id=book_id),
        )
        return FetchedBook(
            source=BookSource.GUTENBERG,
            source_id=book_id,
            source_url=f"https://www.gutenberg.org/ebooks/{book_id}",
            data=await self._download(epub_url),
            rights="Public domain in the USA (Project Gutenberg).",
            cover_url=formats.get("image/jpeg", ""),
            extra={
                "subjects": meta.get("subjects", []),
                "bookshelves": meta.get("bookshelves", []),
                "download_count": meta.get("download_count", 0),
            },
        )

    # ── Standard Ebooks ───────────────────────────────────────

    async def _standard_ebooks(self, ref: str) -> FetchedBook:
        url = urlparse(ref)
        parts = url.path.strip("/").split("/")
        if url.netloc != _SE_HOST or len(parts) < 3 or parts[0] != "ebooks":
            raise ValidationError(
                "A Standard Ebooks book is referenced by its page URL, e.g. "
                "https://standardebooks.org/ebooks/jane-austen/pride-and-prejudice"
            )
        path = "/".join(parts)
        stem = "_".join(parts[1:])
        page = f"https://{_SE_HOST}/{path}"
        return FetchedBook(
            source=BookSource.STANDARD_EBOOKS,
            source_id=page,
            source_url=page,
            data=await self._download(f"{page}/downloads/{stem}.epub?source=download"),
            rights="Public domain in the USA; Standard Ebooks edition dedicated under CC0 1.0.",
        )

    # ── Local file ────────────────────────────────────────────

    @staticmethod
    def _upload(path: str) -> FetchedBook:
        with Path(path).open("rb") as handle:
            data = handle.read(_MAX_BYTES + 1)
        if len(data) > _MAX_BYTES:
            raise ValidationError("That file is too large to be an ebook.")
        # No rights line: there is no source to ask. The operator states one
        # when publishing, and publishing is refused until they do.
        return FetchedBook(
            source=BookSource.UPLOAD,
            source_id=hashlib.sha256(data).hexdigest(),
            source_url="",
            data=data,
        )

    # ── Transport ─────────────────────────────────────────────

    async def _json(self, url: str) -> dict[str, object]:
        try:
            response = await self._client.get(
                url, headers={"User-Agent": _USER_AGENT}, timeout=self._timeout
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("catalogue request failed: %s", type(exc).__name__)
            raise ExternalServiceError("The book catalogue is unavailable right now.") from None
        if not isinstance(body, dict):
            raise ExternalServiceError("The book catalogue answered with an unexpected shape.")
        return body

    async def _download(self, url: str) -> bytes:
        chunks: list[bytes] = []
        size = 0
        try:
            async with self._client.stream(
                "GET",
                url,
                headers={"User-Agent": _USER_AGENT},
                timeout=self._timeout,
                follow_redirects=True,
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > _MAX_BYTES:
                        raise ValidationError("That download is too large to be an ebook.")
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            logger.warning("ebook download failed: %s", type(exc).__name__)
            raise ExternalServiceError("The ebook could not be downloaded right now.") from None
        data = b"".join(chunks)
        if not data.startswith(b"PK"):
            # An HTML interstitial or an error page served with 200. Refuse
            # here rather than let the parser report "not a zip archive".
            raise ExternalServiceError("The source answered with a page, not an ebook.")
        return data
