"""The reader's use cases: open a book, look a word up in context, translate a
paragraph, and remember where the learner is.

The word lookup is the spec's "multi-tiered dictionary cache manager", and most
of it is *not here*, on purpose. A tap goes through the very chain a flashcard
lookup goes through — ``HotCachingAIService`` (Redis) → ``CachingAIService``
(``ai_lookup_entries``) → ``LexiconAIService`` (``lexemes``) → grounded →
failover → provider — so a word a reader taps is a word the deck builder never
pays for, and a word looked up on a flashcard is free to every reader. What
this service adds is exactly the two things the flashcard path does not know:

1. **The lemma.** The reader sees inflected text; the lexicon is keyed by
   dictionary form. Lemmatisation is offline and happens before the chain.
2. **The context.** Of the senses the chain returns, which one this sentence
   uses — decided locally when the sentence says so, by a cheap index-only
   model call when it does not, and memoised per (sense deck, sentence)
   because a public-domain sentence is the same sentence for every reader.

The **sentence never reaches the lexicon**. The chain is called with the lemma
alone, so the shared, impersonal senses it writes are a fact about the word and
not about one learner's paragraph — the same rule that keeps ``interests`` out
of the lookup cache key. That is why a cold, ambiguous word costs two calls
rather than one prompt returning "every sense plus the contextual one": the
first call is paid once per word, ever, and the second once per sentence, for
the whole platform.
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.application.dto import LookupView
from app.application.ports.ai_service import AIService, LearnerContext, LookupResult
from app.application.ports.lemmatizer import Lemmatizer
from app.application.ports.lookup_cache import build_lookup_cache_key, normalize_lookup_input
from app.application.ports.reader_ai import (
    DisambiguationMemo,
    PassageTranslator,
    SenseDisambiguator,
)
from app.core.exceptions import ExternalServiceError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.domain.entities.book import (
    Book,
    BookBlock,
    BookChapter,
    PassageTranslation,
    ReadingPosition,
)
from app.domain.entities.user import User
from app.domain.repositories.book_repository import (
    BookProgressRepository,
    BookRepository,
    PassageTranslationRepository,
)
from app.domain.services.contextual_sense import (
    ContextualChoice,
    ContextualSelection,
    choose_locally,
    sentence_around,
)

logger = get_logger("vocably.reader")

#: Longest tap. A word or short phrase, never a sentence: sentences are context.
MAX_WORD_CHARS = 80
#: Longest client-supplied sentence. Server-derived sentences are bounded by
#: ``sentence_around``; this bounds what a client may claim the sentence was.
MAX_SENTENCE_CHARS = 600
#: Longest free-text paragraph accepted for translation. A book block is
#: whatever length its author wrote, and is trusted; free text is not.
MAX_FREE_TEXT_CHARS = 1_500
#: Below this, the model's own confidence is treated as "none of these".
MIN_MODEL_CONFIDENCE = 0.4


@dataclass(frozen=True, slots=True)
class ReaderLookupView:
    """A flashcard lookup plus the reader's two extra facts."""

    lookup: LookupView
    #: What was tapped, normalised, and the dictionary form it resolved to.
    surface: str
    lemma: str
    #: Index into ``lookup.result.suggestions`` of the sense this sentence
    #: uses, or ``None`` when no stored sense fits.
    contextual_index: int | None
    selection: ContextualSelection
    selection_score: float | None = None


@dataclass(frozen=True, slots=True)
class PassageView:
    translation: str
    target_language: str
    cached: bool


class ReaderService:
    def __init__(
        self,
        *,
        books: BookRepository,
        progress: BookProgressRepository,
        translations: PassageTranslationRepository,
        ai: AIService,
        disambiguator: SenseDisambiguator,
        translator: PassageTranslator,
        lemmatizer: Lemmatizer,
        memo: DisambiguationMemo | None,
        prompt_version: int,
        reader_prompt_version: int,
        provider: str = "",
        model: str = "",
    ) -> None:
        self._books = books
        self._progress = progress
        self._translations = translations
        self._ai = ai
        self._disambiguator = disambiguator
        self._translator = translator
        self._lemmatizer = lemmatizer
        self._memo = memo
        self._prompt_version = prompt_version
        self._reader_prompt_version = reader_prompt_version
        self._provider = provider
        self._model = model

    # ── Books ─────────────────────────────────────────────────

    async def list_books(
        self, *, language: str | None, limit: int, offset: int
    ) -> tuple[list[Book], int]:
        return await self._books.list_public(language=language, limit=limit, offset=offset)

    async def get_book(self, book_id: UUID, user: User) -> tuple[Book, ReadingPosition | None]:
        book = await self._readable(book_id, user)
        return book, await self._progress.get(user.id, book_id)

    async def get_chapter(
        self, book_id: UUID, chapter_id: UUID, user: User, *, after: int, limit: int
    ) -> BookChapter:
        await self._readable(book_id, user)
        chapter = await self._books.get_chapter(book_id, chapter_id, after=after, limit=limit)
        if chapter is None:
            raise NotFoundError("Chapter not found.")
        return chapter

    async def _readable(self, book_id: UUID, user: User) -> Book:
        """A public book, or any book for an admin previewing it before publish.

        A private book answers 404 to everyone else — never 403, which would
        confirm to a probe that the id exists.
        """
        book = await self._books.get(book_id)
        if book is None or not (book.is_public or user.is_admin):
            raise NotFoundError("Book not found.")
        return book

    # ── Lookup ────────────────────────────────────────────────

    async def look_up(
        self,
        user: User,
        *,
        word: str,
        sentence: str = "",
        block_id: UUID | None = None,
        char_start: int | None = None,
        char_end: int | None = None,
    ) -> ReaderLookupView:
        surface = normalize_lookup_input(word)
        if not surface:
            raise ValidationError("Tap a word to look it up.")
        if len(surface) > MAX_WORD_CHARS:
            raise ValidationError("Select a word or a short phrase, not a whole sentence.")

        sentence = await self._resolve_sentence(user, sentence, block_id, char_start, char_end)
        learner = _learner_context(user)
        lemma = self._lemmatizer.lemma(surface, language="en") or surface

        result = await self._ai.look_up_meanings(lemma, learner)
        if not result.suggestions and lemma != surface:
            # The lemmatiser can be wrong ("saw" → "see" in a sentence about a
            # tool). One retry with what was actually tapped; still one word.
            result = await self._ai.look_up_meanings(surface, learner)

        lookup_id = self._lookup_id(result.term, learner)
        choice = await self._choose(result, lookup_id, sentence, learner)
        return ReaderLookupView(
            lookup=LookupView(result=result, lookup_id=lookup_id),
            surface=surface,
            lemma=lemma,
            contextual_index=choice.index,
            selection=choice.selection,
            selection_score=choice.score,
        )

    async def _choose(
        self, result: LookupResult, lookup_id: str, sentence: str, learner: LearnerContext
    ) -> ContextualChoice:
        senses = result.suggestions
        if not senses:
            return ContextualChoice(None, ContextualSelection.NONE)
        local = choose_locally(result.term, sentence, senses)
        if local is not None:
            return local
        if not sentence:
            return ContextualChoice(0, ContextualSelection.FIRST)

        key = ""
        if self._memo is not None:
            key = self._memo.disambiguation_key(lookup_id, sentence, self._reader_prompt_version)
            cached = await self._memo.get_disambiguation(key)
            if cached is not None:
                return _from_index(cached, len(senses))

        try:
            answer = await self._disambiguator.disambiguate_sense(
                result.term, sentence, senses, learner
            )
        except ExternalServiceError:
            # Money, never correctness: an outage degrades to the most common
            # sense, which is exactly what a flashcard would have shown.
            logger.warning("disambiguation unavailable; falling back to the first sense")
            return ContextualChoice(0, ContextualSelection.FIRST)

        index = answer.index if answer.confidence >= MIN_MODEL_CONFIDENCE else -1
        if self._memo is not None:
            await self._memo.put_disambiguation(key, index)
        return _from_index(index, len(senses), answer.confidence)

    async def _resolve_sentence(
        self,
        user: User,
        claimed: str,
        block_id: UUID | None,
        char_start: int | None,
        char_end: int | None,
    ) -> str:
        """Prefer the sentence the *server* can derive from the block.

        It is trustworthy, it is canonical — so the disambiguation memo is
        shared by every learner who taps the same line — and it costs one
        indexed read. The client's own sentence is used only when there is no
        block to read it from, or the offsets do not fit the block.
        """
        if block_id is not None and char_start is not None and char_end is not None:
            found = await self._books.get_block(block_id)
            if found is not None:
                book, _, block = found
                readable = book.is_public or user.is_admin
                if readable and 0 <= char_start < char_end <= len(block.text):
                    return sentence_around(block.text, char_start, char_end)
        return " ".join(claimed.split())[:MAX_SENTENCE_CHARS]

    def _lookup_id(self, resolved_term: str, learner: LearnerContext) -> str:
        """Identical to ``AIStudioService._lookup_id``: the same deck of senses
        is rated through ``POST /ai/feedback`` whichever screen showed it."""
        if not resolved_term:
            return ""
        return build_lookup_cache_key(resolved_term, learner, self._prompt_version).digest()

    # ── Translation ───────────────────────────────────────────

    async def translate(
        self,
        user: User,
        *,
        block_id: UUID | None = None,
        text: str = "",
        target_language: str | None = None,
    ) -> PassageView:
        target = (target_language or user.native_language).strip() or "English"
        preceding = ""
        book_title = ""

        if block_id is not None:
            found = await self._books.get_block(block_id)
            if found is None or not (found[0].is_public or user.is_admin):
                raise NotFoundError("Passage not found.")
            book, chapter, block = found
            text = block.text
            book_title = book.title
            preceding = await self._preceding_text(book, chapter, block)
            cacheable = True
        else:
            # The same NFC + whitespace collapse the ingest applies, so a
            # paragraph copied out of a book hashes to its block.
            text = " ".join(unicodedata.normalize("NFC", text).split())
            if not text:
                raise ValidationError("There is nothing to translate.")
            if len(text) > MAX_FREE_TEXT_CHARS:
                raise ValidationError("Select one paragraph at a time.")
            # Free text is stored only when it is provably a book's paragraph.
            # Anything else might be the learner's own, and never lands in a
            # shared table — the rule ``MAX_ALIAS_INPUT_CHARS`` applies to the
            # lookup cache for the same reason.
            cacheable = bool(await self._books.get_blocks_by_hash(_sha256(text)))
            # Measures whether free text repeats enough to deserve a per-user
            # cache. No text reaches the log: only a fingerprint salted with the
            # user id, so repeats are countable by grouping on it and nothing is
            # comparable across users.
            logger.info(
                "free-text translation fp=%s chars=%d is_book_text=%s",
                _sha256(f"{user.id}\x1f{text}")[:12],
                len(text),
                cacheable,
            )

        digest = _sha256(text)
        if cacheable:
            try:
                hit = await self._translations.get(
                    digest, target_language=target, prompt_version=self._reader_prompt_version
                )
            except Exception:  # noqa: BLE001 — a cache fault is a miss
                logger.warning("passage cache read failed; translating", exc_info=True)
                hit = None
            if hit is not None:
                return PassageView(hit.translation, target, cached=True)

        answer = await self._translator.translate_passage(
            text, target, preceding=preceding, book_title=book_title
        )
        if cacheable:
            try:
                await self._translations.put(
                    PassageTranslation(
                        text_hash=digest,
                        target_language=target,
                        prompt_version=self._reader_prompt_version,
                        translation=answer.translation,
                        provider=answer.provider or self._provider,
                        model=answer.model or self._model,
                    )
                )
            except Exception:  # noqa: BLE001
                logger.warning("passage cache write failed; served uncached", exc_info=True)
        return PassageView(answer.translation, target, cached=False)

    async def _preceding_text(self, book: Book, chapter: BookChapter, block: BookBlock) -> str:
        """The previous paragraph, for pronoun and tense continuity.

        Derived from the block's *position*, so it is a deterministic function
        of the block — which is what lets the translation be cached by text
        hash without the context varying between callers.
        """
        if block.position == 0:
            return ""
        previous = await self._books.get_chapter(
            book.id, chapter.id, after=block.position - 2, limit=1
        )
        if previous is None or not previous.blocks:
            return ""
        return previous.blocks[0].text[:800]

    # ── Progress ──────────────────────────────────────────────

    async def sync_position(
        self, user: User, *, book_id: UUID, block_id: UUID, char_offset: int = 0
    ) -> ReadingPosition:
        book = await self._readable(book_id, user)
        found = await self._books.get_block(block_id)
        if found is None or found[0].id != book.id:
            raise ValidationError("That passage is not in this book.")
        _, chapter, block = found
        return await self._progress.upsert(
            ReadingPosition(
                user_id=user.id,
                book_id=book.id,
                chapter_id=chapter.id,
                block_id=block.id,
                char_offset=min(max(char_offset, 0), len(block.text)),
                percent=book.percent_at(chapter, block, char_offset),
                updated_at=datetime.now(UTC),
            )
        )

    async def shelf(self, user: User, *, limit: int = 20) -> list[ReadingPosition]:
        return await self._progress.list_for_user(user.id, limit=limit)

    async def remove_from_shelf(self, user: User, book_id: UUID) -> None:
        if not await self._progress.delete(user.id, book_id):
            raise NotFoundError("That book is not on your shelf.")


def _from_index(index: int, count: int, score: float | None = None) -> ContextualChoice:
    if 0 <= index < count:
        return ContextualChoice(index, ContextualSelection.MODEL, score)
    # -1 from the model, or an index outside the list it was shown: the
    # lexicon lacks this sense. Say so honestly rather than guess.
    return ContextualChoice(None, ContextualSelection.NONE, score)


def _learner_context(user: User) -> LearnerContext:
    return LearnerContext(
        native_language=user.native_language,
        age_range=user.age_range.value if user.age_range else None,
        interests=tuple(user.interests),
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
