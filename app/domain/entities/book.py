"""A book, as the reader sees it: chapters of blocks, and one learner's place in it.

The unit of text is the **block** — a paragraph, a heading, a quotation or a
run of verse — and deliberately not the page or the token:

* A *page* is a fact about a screen. It depends on font size, device width
  and the learner's accessibility settings, so the server cannot know where
  one ends. The client paginates blocks; the server never stores pages.
* A *token table* would be one row per word of every book — a hundred
  thousand rows for one novel — for no query that needs them. A tap is
  addressed as ``(block_id, char_start, char_end)`` against the block's
  canonical text, which is immutable once ingested (``Book.content_hash``
  says so), so the same offsets mean the same word forever.

Nothing here is user data except :class:`ReadingPosition`. Book text is public
domain by construction (``Book.rights`` records why), and one copy serves
everybody, exactly as the lexicon does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.enums import BlockKind, BookSource


@dataclass(slots=True)
class BookBlock:
    id: UUID = field(default_factory=uuid4)
    chapter_id: UUID = field(default_factory=uuid4)
    #: 0-based, contiguous within the chapter. The reading order.
    position: int = 0
    kind: BlockKind = BlockKind.PARAGRAPH
    #: Canonical text: NFC, whitespace-collapsed, footnote markers removed.
    #: Offsets a client sends are measured against exactly this string.
    text: str = ""
    #: sha256 of ``text``. The key a passage translation is cached under, so
    #: the same paragraph in two editions of one book is translated once.
    text_hash: str = ""
    word_count: int = 0
    #: Words in this chapter before this block. With the chapter's own
    #: ``words_before``, a reading position becomes a percentage in one
    #: subtraction rather than a scan.
    words_before: int = 0


@dataclass(slots=True)
class BookChapter:
    id: UUID = field(default_factory=uuid4)
    book_id: UUID = field(default_factory=uuid4)
    #: 0-based, contiguous. The table of contents order.
    index: int = 0
    title: str = ""
    #: "Book One", "Part II" — the division this chapter sits under, when the
    #: source has them. Presentation only; nothing is keyed by it.
    part_title: str = ""
    word_count: int = 0
    block_count: int = 0
    #: Words in the book before this chapter.
    words_before: int = 0
    blocks: list[BookBlock] = field(default_factory=list)


@dataclass(slots=True)
class Book:
    id: UUID = field(default_factory=uuid4)
    #: URL-safe, unique. Derived from the source's own identifier so two
    #: ingests of one edition collide rather than duplicate.
    slug: str = ""
    title: str = ""
    author: str = ""
    #: ISO 639-1 of the *text* ("en"). Not the learner's language.
    language: str = "en"
    description: str = ""
    cover_url: str = ""
    source: BookSource = BookSource.UPLOAD
    #: The source's own id: a Gutenberg number, a Standard Ebooks page URL,
    #: or an upload's sha256. Unique with ``source``.
    source_id: str = ""
    source_url: str = ""
    #: Why this text may be served: the licence line the source publishes.
    #: Free text on purpose — it is read by a human deciding whether to
    #: publish, never by code.
    rights: str = ""
    #: Anything else the source gave us that nothing queries: subjects,
    #: original publication year, translator. Opaque to SQL.
    extra: dict[str, object] = field(default_factory=dict)
    #: sha256 over every block of every chapter, in order. Two ingests of the
    #: same file are a no-op; a *different* file for a published book is
    #: refused, because block ids and reading positions point into this text.
    content_hash: str = ""
    total_chapters: int = 0
    total_words: int = 0
    is_public: bool = False
    published_at: datetime | None = None
    chapters: list[BookChapter] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def percent_at(self, chapter: BookChapter, block: BookBlock, char_offset: int = 0) -> int:
        """How far through the book a position is, by words read, 0..100.

        Counts the block's own words in proportion to ``char_offset``, so a
        learner at the end of the last paragraph reads 100 rather than stopping
        one paragraph short of it.
        """
        if self.total_words <= 0:
            return 0
        into_block = 0.0
        if block.text:
            into_block = (
                block.word_count * min(max(char_offset, 0), len(block.text)) / len(block.text)
            )
        read = chapter.words_before + block.words_before + into_block
        return max(0, min(100, round(read * 100 / self.total_words)))


@dataclass(slots=True)
class ReadingPosition:
    """Where one learner is in one book. The only user data in this module.

    One row per ``(user, book)``, overwritten on every sync — a position is a
    fact about *now*, and a history of it is not a product. ``percent`` is
    computed server-side from word counts when the row is written, so two
    devices with different fonts agree on it.
    """

    user_id: UUID = field(default_factory=uuid4)
    book_id: UUID = field(default_factory=uuid4)
    chapter_id: UUID = field(default_factory=uuid4)
    block_id: UUID = field(default_factory=uuid4)
    #: Offset into the block's canonical text. Zero is the ordinary case;
    #: clients that position by block need not send anything finer.
    char_offset: int = 0
    percent: int = 0
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True)
class PassageTranslation:
    """One paragraph, translated once for everyone who reads it.

    Keyed by the text's hash, the target language and the prompt version —
    never by who asked. Only text that is a block of a stored book is cached
    (see ``ReaderService.translate``): the endpoint also accepts free text,
    and free text is the one input that might carry something personal.
    """

    text_hash: str = ""
    target_language: str = ""
    prompt_version: int = 0
    translation: str = ""
    provider: str = ""
    model: str = ""
    hit_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
