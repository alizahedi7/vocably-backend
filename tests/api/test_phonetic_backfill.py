"""``vocably.ai.backfill_phonetics``: filling in the IPA cards were saved without.

The behaviour worth pinning down is not "it writes a column" — it is the three
distinct outcomes of a lookup, because two of them look identical from the
outside and only one of them may be recorded:

* an answer with a transcription → store it;
* an answer *without* one (a third of words) → store ``""``, so the term leaves
  the backlog instead of being re-fetched nightly forever;
* **no answer** — a miss or an outage, indistinguishable through the port →
  store nothing, or a bad afternoon would permanently silence a set of cards.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports.dictionary_service import DictionaryEntry, DictionarySense
from app.application.services.phonetic_backfill_service import (
    MAX_PHONETIC_CHARS,
    PhoneticBackfillService,
)
from app.infrastructure.db.models.deck import DeckModel
from app.infrastructure.db.models.word import WordModel
from app.infrastructure.db.repositories.word_repository import SqlAlchemyWordRepository
from tests.api.conftest import UserFactory


class FakeDictionary:
    """Answers from a table; anything absent from it returns ``None``.

    ``None`` deliberately covers both "no such word" and "the API is down",
    exactly as the real port does — that conflation is what the service has to
    be careful with.
    """

    def __init__(self, entries: dict[str, DictionaryEntry | None]) -> None:
        self.entries = entries
        self.asked: list[str] = []

    async def look_up(self, term: str) -> DictionaryEntry | None:
        self.asked.append(term)
        return self.entries.get(term)


def entry(term: str, phonetic: str) -> DictionaryEntry:
    return DictionaryEntry(
        term=term,
        senses=[DictionarySense(definition=f"to {term}")],
        phonetic=phonetic,
    )


async def seed_words(
    session_factory: async_sessionmaker[AsyncSession],
    make_user: UserFactory,
    terms: list[str],
    *,
    phonetic: str | None = None,
) -> uuid.UUID:
    """One user, one deck, one card per term. Returns the deck id.

    Rows are written directly rather than through the API because the backfill
    exists for cards the API cannot produce: ones saved before the column, or
    without a lookup behind them.
    """
    user = await make_user()
    async with session_factory() as session:
        deck = DeckModel(id=uuid.uuid4(), user_id=user.id, name="Backfill", hue=262)
        session.add(deck)
        await session.flush()
        for term in terms:
            session.add(
                WordModel(
                    id=uuid.uuid4(),
                    deck_id=deck.id,
                    created_by_user_id=user.id,
                    term=term,
                    meaning="—",
                    phonetic=phonetic,
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
            )
        await session.commit()
        return deck.id


async def phonetics_by_term(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, str | None]:
    async with session_factory() as session:
        rows = (await session.execute(select(WordModel.term, WordModel.phonetic))).all()
    return dict(rows)  # type: ignore[arg-type]


async def run_backfill(
    session_factory: async_sessionmaker[AsyncSession],
    dictionary: FakeDictionary,
    *,
    limit: int = 50,
):  # noqa: ANN201 — BackfillResult
    async with session_factory() as session:
        result = await PhoneticBackfillService(SqlAlchemyWordRepository(session), dictionary).run(
            limit=limit
        )
        await session.commit()
    return result


async def test_fills_in_what_the_dictionary_knows(
    session_factory: async_sessionmaker[AsyncSession], make_user: UserFactory
) -> None:
    await seed_words(session_factory, make_user, ["undermine", "keen"])
    dictionary = FakeDictionary(
        {
            "undermine": entry("undermine", "/ʌndəˈmaɪn/"),
            "keen": entry("keen", "/kiːn/"),
        }
    )

    result = await run_backfill(session_factory, dictionary)

    assert result.terms_found == 2
    assert result.words_updated == 2
    assert await phonetics_by_term(session_factory) == {
        "undermine": "/ʌndəˈmaɪn/",
        "keen": "/kiːn/",
    }


async def test_a_word_with_no_ipa_is_asked_about_once(
    session_factory: async_sessionmaker[AsyncSession], make_user: UserFactory
) -> None:
    """The empty string is the memo that stops a nightly re-fetch forever."""
    await seed_words(session_factory, make_user, ["gaslight"])
    dictionary = FakeDictionary({"gaslight": entry("gaslight", "")})

    first = await run_backfill(session_factory, dictionary)
    assert first.terms_checked == 1
    assert first.terms_found == 0
    assert await phonetics_by_term(session_factory) == {"gaslight": ""}

    second = await run_backfill(session_factory, dictionary)
    assert second.exhausted, "a covered-but-untranscribed word must leave the backlog"
    assert dictionary.asked == ["gaslight"]


async def test_an_unanswered_lookup_is_never_recorded_as_having_no_ipa(
    session_factory: async_sessionmaker[AsyncSession], make_user: UserFactory
) -> None:
    """A miss and an outage are the same ``None`` here, so neither may be stored.

    Writing ``""`` on this path would make one bad afternoon permanently silence
    every card looked up during it.
    """
    await seed_words(session_factory, make_user, ["blorptastic"])
    dictionary = FakeDictionary({})

    result = await run_backfill(session_factory, dictionary)

    assert result.terms_unanswered == 1
    assert result.words_updated == 0
    assert await phonetics_by_term(session_factory) == {"blorptastic": None}
    # Still in the backlog, so a dictionary that recovers gets another chance.
    assert not (await run_backfill(session_factory, dictionary)).exhausted


async def test_one_lookup_answers_for_every_learners_copy_of_the_word(
    session_factory: async_sessionmaker[AsyncSession], make_user: UserFactory
) -> None:
    """Keyed by term, not by card — a class of thirty is one dictionary call."""
    await seed_words(session_factory, make_user, ["Run", "run", "  run  "])
    dictionary = FakeDictionary({"run": entry("run", "/rʌn/")})

    result = await run_backfill(session_factory, dictionary)

    assert dictionary.asked == ["run"], "casing must not multiply the requests"
    assert result.words_updated == 3
    assert set((await phonetics_by_term(session_factory)).values()) == {"/rʌn/"}


async def test_a_transcription_that_arrived_with_the_card_is_never_overwritten(
    session_factory: async_sessionmaker[AsyncSession], make_user: UserFactory
) -> None:
    await seed_words(session_factory, make_user, ["run"], phonetic="/rʌn/")
    dictionary = FakeDictionary({"run": entry("run", "/WRONG/")})

    result = await run_backfill(session_factory, dictionary)

    assert result.exhausted
    assert dictionary.asked == []
    assert await phonetics_by_term(session_factory) == {"run": "/rʌn/"}


async def test_a_failing_dictionary_leaves_the_cards_alone(
    session_factory: async_sessionmaker[AsyncSession], make_user: UserFactory
) -> None:
    """A backfill must not crash the worker, and must not guess."""
    await seed_words(session_factory, make_user, ["run"])

    class Exploding(FakeDictionary):
        async def look_up(self, term: str) -> DictionaryEntry | None:
            raise TimeoutError("upstream is gone")

    result = await run_backfill(session_factory, Exploding({}))

    assert result.terms_unanswered == 1
    assert await phonetics_by_term(session_factory) == {"run": None}


@pytest.mark.parametrize("length", [MAX_PHONETIC_CHARS + 1, MAX_PHONETIC_CHARS * 3])
async def test_an_absurdly_long_answer_is_not_a_transcription(
    session_factory: async_sessionmaker[AsyncSession], make_user: UserFactory, length: int
) -> None:
    """It would not fit the column, and it is some other field leaking in."""
    await seed_words(session_factory, make_user, ["run"])
    dictionary = FakeDictionary({"run": entry("run", "x" * length)})

    result = await run_backfill(session_factory, dictionary)

    assert result.terms_found == 0
    assert await phonetics_by_term(session_factory) == {"run": ""}


async def test_the_batch_size_bounds_one_run(
    session_factory: async_sessionmaker[AsyncSession], make_user: UserFactory
) -> None:
    """The dictionary has a rate limit; the backlog does not have a deadline."""
    terms = [f"word{i}" for i in range(10)]
    await seed_words(session_factory, make_user, terms)
    dictionary = FakeDictionary({t: entry(t, f"/{t}/") for t in terms})

    result = await run_backfill(session_factory, dictionary, limit=3)

    assert result.terms_checked == 3
    assert len(dictionary.asked) == 3
    remaining = [t for t, p in (await phonetics_by_term(session_factory)).items() if p is None]
    assert len(remaining) == 7
