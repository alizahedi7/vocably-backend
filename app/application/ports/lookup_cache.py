"""Port: the shared cache of AI lookup results.

Vocabulary lookups are heavily repeated across the user base — "ephemeral" is the
same word for every learner — so the same provider response is bought over and
over at full price. This port lets a cache sit in front of any
:class:`~app.application.ports.ai_service.AIService` adapter.

Three properties define the contract, and every implementation must honour them:

* **The cache is impersonal.** Nothing here is keyed by, or carries, a user id.
  A cached entry is a dictionary fact about a term, not a record of who looked it
  up.
* **The cache only ever holds what a provider produced.** A learner's own edits
  to a card live in ``words``; writing them back here would serve one person's
  wording to everybody and make entries non-reproducible.
* **The key is the request, not the outcome.** A lookup is cached with every
  sense the provider returned, before the learner picks one — the next learner
  needs the whole deck to choose from.

See :class:`LookupCacheKey` for what "the request" reduces to, and why
``interests`` is deliberately not part of it.
"""

from __future__ import annotations

import hashlib
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.application.ports.ai_service import LearnerContext, LookupResult
from app.domain.enums import AgeRange

#: Longest raw input that earns an alias row. Anything longer is a sentence, not
#: a term: its chance of being typed identically by another learner rounds to
#: zero, so an alias would be dead weight in the hot index. The *resolved* term
#: is still cached — a sentence lookup still pays for the next learner who looks
#: up the word it contained. Doubles as a privacy bound: long free text is the
#: only lookup input likely to carry something personal, and it never lands in
#: this shared, cross-user table.
MAX_ALIAS_INPUT_CHARS: Final = 40


class CacheAgeBucket(StrEnum):
    """Age collapsed to the granularity that actually changes model output.

    ``AgeRange`` has eight values because onboarding asks a demographic question.
    The prompt only uses age for appropriateness, and a 25-34 learner gets the
    same dictionary entry as a 45-54 one — so keying the cache on all eight would
    shard it eight ways for no difference in the cached text. Three buckets keep
    the one distinction that is real.
    """

    CHILD = "child"
    TEEN = "teen"
    ADULT = "adult"

    @classmethod
    def from_age_range(cls, age_range: str | None) -> CacheAgeBucket:
        """Map a raw ``AgeRange`` value (or ``None``) onto a cache bucket.

        Unknown values — an older client, or a value added to ``AgeRange`` that
        nobody mapped here — fall back to ``ADULT`` rather than raising, because
        a cache key is never worth failing a lookup over.
        """
        if age_range == AgeRange.UNDER_13.value:
            return cls.CHILD
        if age_range == AgeRange.TEEN.value:
            return cls.TEEN
        return cls.ADULT


def normalize_lookup_input(text: str) -> str:
    """Reduce raw input to the form the cache keys on.

    Conservative on purpose. Case, surrounding punctuation and whitespace noise
    are accidents of typing and should collide; anything else is a different
    lookup. In particular there is **no stemming or lemmatising** — "running" and
    "run" are legitimately different cards, and merging them would serve the
    wrong one.

    Case folding does merge a handful of genuine pairs (``Polish``/``polish``,
    ``March``/``march``). That is accepted: the provider returns every sense of
    the written form anyway, so both readings are usually already on the deck.
    """
    folded = unicodedata.normalize("NFKC", text).strip()
    folded = " ".join(folded.split())
    folded = folded.strip("\"'“”‘’.,!?;:()[]{}")
    return folded.casefold()


@dataclass(frozen=True, slots=True)
class LookupCacheKey:
    """Everything that changes a cached lookup's text, and nothing that doesn't.

    * ``normalized_input`` — see :func:`normalize_lookup_input`.
    * ``native_language`` — ``native_meaning`` is written in it.
    * ``age_bucket`` — the prompt varies appropriateness by it.
    * ``prompt_version`` — see :data:`~app.infrastructure.ai.prompts.PROMPT_VERSION`.

    The detected language of the term is deliberately absent: the prompt derives
    it from the input, so it is a function of ``normalized_input`` already.

    ``LearnerContext.interests`` is deliberately absent too, and it is the one
    real trade in this design. Interests theme the examples, so including them
    would make every key near-unique and the cache would stop existing. Cached
    cards therefore carry good but un-themed examples. If themed examples later
    prove worth paying for, the upgrade is to split the payload — cache the
    dictionary layer, which is genuinely universal, and generate only the
    examples per request.
    """

    normalized_input: str
    native_language: str
    age_bucket: CacheAgeBucket
    prompt_version: int

    @property
    def is_aliasable(self) -> bool:
        """Whether this input is short enough to be worth an alias row."""
        return 0 < len(self.normalized_input) <= MAX_ALIAS_INPUT_CHARS

    def digest(self) -> str:
        """A fixed-width, collation-independent hash of the key.

        Indexed instead of the raw columns so the unique index stays narrow and
        behaves identically regardless of the database's text collation — which
        matters once terms arrive in every script the app supports. The plain
        columns are stored alongside it for debugging and analytics.
        """
        raw = "\x1f".join(
            (
                str(self.prompt_version),
                self.native_language.casefold(),
                self.age_bucket.value,
                self.normalized_input,
            )
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def for_term(self, term: str) -> LookupCacheKey:
        """The key of the *entry* this lookup resolved to.

        A typo, a sentence, and the clean word all resolve to one term and so
        share a single paid-for entry.
        """
        return LookupCacheKey(
            normalized_input=normalize_lookup_input(term),
            native_language=self.native_language,
            age_bucket=self.age_bucket,
            prompt_version=self.prompt_version,
        )


def build_lookup_cache_key(
    term: str,
    learner: LearnerContext,
    prompt_version: int,
) -> LookupCacheKey:
    return LookupCacheKey(
        normalized_input=normalize_lookup_input(term),
        native_language=learner.native_language,
        age_bucket=CacheAgeBucket.from_age_range(learner.age_range),
        prompt_version=prompt_version,
    )


class LookupCacheRepository(ABC):
    @abstractmethod
    async def get(self, key: LookupCacheKey) -> LookupResult | None:
        """The cached result for ``key``, or ``None`` on a miss.

        A stored payload that no longer validates counts as a miss rather than an
        error: the shape of what we persisted is no more trustworthy than the
        shape of what a provider returns.
        """

    @abstractmethod
    async def put(
        self,
        key: LookupCacheKey,
        result: LookupResult,
        *,
        alias_ttl_seconds: int | None = None,
        provider: str = "",
        model: str = "",
    ) -> None:
        """Store ``result`` under ``key``.

        Writes the senses once under the resolved term and a small alias row for
        the raw input, so spelling variants share one entry. Implementations must
        tolerate a concurrent writer storing the same key — two learners looking
        up a brand-new word at the same moment is normal and costs one duplicate
        provider call, not an error.

        ``alias_ttl_seconds`` expires the alias only; entries never expire, since
        the meaning of a word does not. Used to keep unintelligible input from
        accumulating forever while still absorbing retry-spam.

        ``provider`` and ``model`` are recorded for provenance. They are
        deliberately *not* part of the key: switching models is not a reason to
        re-buy a corpus that is already correct.
        """
