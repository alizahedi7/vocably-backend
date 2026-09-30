"""Reader request/response schemas. snake_case, like every learner-facing route."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.v1.schemas.ai import LookupOut, MeaningSuggestionOut
from app.application.services.reader_service import PassageView, ReaderLookupView
from app.domain.entities.book import Book, BookChapter, ReadingPosition
from app.domain.enums import BlockKind
from app.domain.services.contextual_sense import ContextualSelection


class BookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    title: str
    author: str
    language: str
    description: str
    #: Empty when the source shipped none. Render a placeholder, not a broken image.
    cover_url: str
    total_chapters: int
    total_words: int
    published_at: datetime | None


class BookPageOut(BaseModel):
    items: list[BookOut]
    total: int


class ChapterSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    index: int
    title: str
    part_title: str
    word_count: int
    block_count: int


class ReadingPositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    book_id: UUID
    chapter_id: UUID
    block_id: UUID
    char_offset: int
    percent: int
    updated_at: datetime


class BookDetailOut(BookOut):
    """The book, its table of contents, and the caller's place in it.

    One request opens a book. ``progress`` is null for a book the learner has
    never opened, and the client starts at chapter 0, block 0.
    """

    rights: str
    chapters: list[ChapterSummaryOut]
    progress: ReadingPositionOut | None

    @classmethod
    def from_book(cls, book: Book, position: ReadingPosition | None) -> BookDetailOut:
        return cls(
            id=book.id,
            slug=book.slug,
            title=book.title,
            author=book.author,
            language=book.language,
            description=book.description,
            cover_url=book.cover_url,
            total_chapters=book.total_chapters,
            total_words=book.total_words,
            published_at=book.published_at,
            rights=book.rights,
            chapters=[ChapterSummaryOut.model_validate(c) for c in book.chapters],
            progress=ReadingPositionOut.model_validate(position) if position else None,
        )


class BlockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    position: int
    kind: BlockKind
    #: Canonical text. Offsets sent to ``/reader/lookup-word`` and
    #: ``/reader/progress`` index into exactly this string, so the client must
    #: not normalise it before measuring. ``verse`` blocks contain ``\n``.
    text: str
    word_count: int


class ChapterOut(BaseModel):
    id: UUID
    index: int
    title: str
    part_title: str
    word_count: int
    block_count: int
    blocks: list[BlockOut]
    #: Position of the last block returned, to pass as ``after`` for the next
    #: page. Null once the chapter is exhausted.
    next_after: int | None

    @classmethod
    def from_chapter(cls, chapter: BookChapter) -> ChapterOut:
        blocks = chapter.blocks
        last = blocks[-1].position if blocks else None
        exhausted = last is None or last >= chapter.block_count - 1
        return cls(
            id=chapter.id,
            index=chapter.index,
            title=chapter.title,
            part_title=chapter.part_title,
            word_count=chapter.word_count,
            block_count=chapter.block_count,
            blocks=[BlockOut.model_validate(b) for b in blocks],
            next_after=None if exhausted else last,
        )


def _uuid_or_none(value: object) -> object:
    """An id the server cannot parse is no id, not a 422.

    ``book_id`` and ``block_id`` are hints, and a tap must not fail on a hint.
    The app also holds stories of its own, with its own ids, and sends them
    along with a tap in exactly the same fields; the first production tap on
    one of those answered "Input should be a valid UUID" instead of a meaning.
    Unparseable ids fall back to ``sentence_context``, as offsets that do not
    fit a block already do.
    """
    if value is None or isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except ValueError:
        return None


class LookupWordIn(BaseModel):
    """A tap. ``word`` is required; everything else sharpens the answer.

    Send ``block_id`` with ``char_start``/``char_end`` whenever the tap was in a
    book: the server reads the sentence from the canonical text itself, which is
    what lets one disambiguation serve every reader of that line. Send
    ``sentence_context`` for text the server does not hold, such as the app's
    own stories; any id that is not one of the server's is ignored.
    """

    word: str = Field(min_length=1, max_length=120, examples=["bound"])
    sentence_context: str = Field(default="", max_length=600)
    #: Accepted for the spec's shape and for analytics; the block already
    #: names its book, so nothing is decided by it.
    book_id: UUID | None = None
    block_id: UUID | None = None
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=1)

    _lenient_ids = field_validator("book_id", "block_id", mode="before")(_uuid_or_none)


class LookupWordOut(BaseModel):
    """The one meaning the reader is shown, plus the full ``LookupOut`` deck
    that ``POST /ai/lookup`` returns and ``POST /ai/feedback`` rates.

    The reader renders ``meaning`` and nothing else: the other senses are
    stored for the flashcard features, and a reader who tapped a word in a
    sentence has no use for the meanings that sentence does not carry.
    """

    lookup: LookupOut
    surface: str
    lemma: str
    #: What the word means in this sentence. A stored sense when one fits;
    #: the model's own answer for this sentence when none does, in which case
    #: ``contextual_index`` is null and ``example`` is the sentence. Null only
    #: when there is nothing to show at all.
    meaning: MeaningSuggestionOut | None
    #: Index into ``lookup.suggestions`` when ``meaning`` is a stored sense.
    contextual_index: int | None
    #: How ``meaning`` was chosen. ``first`` means no sentence was available or
    #: the model could not be asked, and deserves a "probably".
    selection: ContextualSelection
    selection_score: float | None

    @classmethod
    def from_view(cls, view: ReaderLookupView) -> LookupWordOut:
        return cls(
            lookup=LookupOut.from_dto(view.lookup),
            surface=view.surface,
            lemma=view.lemma,
            meaning=MeaningSuggestionOut.from_dto(view.meaning) if view.meaning else None,
            contextual_index=view.contextual_index,
            selection=view.selection,
            selection_score=view.selection_score,
        )


class TranslateParagraphIn(BaseModel):
    """One of ``block_id`` or ``paragraph_text``. Prefer the block: it is
    cached for everyone, and it carries the previous paragraph as context.

    ``target_language`` is spelled as the profile spells it ("Persian") and
    defaults to the learner's native language. Not a BCP-47 tag, for the reason
    ``lexeme_sense_translations.native_language`` gives.
    """

    block_id: UUID | None = None
    paragraph_text: str = Field(default="", max_length=1_500)
    target_language: str | None = Field(default=None, max_length=64)

    #: A block id that is not the server's is no block: the text is used. The
    #: app's own stories carry their own ids, and it sends them here too.
    _lenient_ids = field_validator("block_id", mode="before")(_uuid_or_none)


class TranslateParagraphOut(BaseModel):
    translation: str
    target_language: str
    #: True when served from the shared cache. Diagnostic only.
    cached: bool

    @classmethod
    def from_view(cls, view: PassageView) -> TranslateParagraphOut:
        return cls(
            translation=view.translation, target_language=view.target_language, cached=view.cached
        )


class ProgressIn(BaseModel):
    book_id: UUID
    block_id: UUID
    char_offset: int = Field(default=0, ge=0)
