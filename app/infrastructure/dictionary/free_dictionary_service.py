"""``DictionaryService`` backed by api.dictionaryapi.dev, with a Redis cache.

A free Wiktionary mirror. Fast when it works — 27 ms median, 325 ms p95 measured
from the production host — but it is free infrastructure and behaves like it:

* **It 403s the default ``python-urllib``/``httpx`` User-Agent.** Every request
  fails in a way that reads as "word not found". The header below is not
  optional decoration.
* **It 429s under a burst.** 58 sequential requests were throttled within
  seconds, which is why the Redis layer here is about *rate limiting*, not
  latency — 27 ms needs no cache.
* **It returns intermittent 502s.** The same term answered 200 and, minutes
  later, 502.

Because entries are impersonal, immutable facts about English, the cache is
shared across every learner and can live a long time. That makes it the single
most effective defence against the rate limit: a term is fetched once per TTL
for the whole user base.
"""

from __future__ import annotations

import json
from typing import Any, Final
from urllib.parse import quote

import httpx

from app.application.ports.dictionary_service import (
    DictionaryEntry,
    DictionarySense,
    DictionaryService,
)
from app.core.logging import get_logger

logger = get_logger("vocably.dictionary")

_BASE: Final = "https://api.dictionaryapi.dev/api/v2/entries/en/"

#: Cloudflare in front of the API rejects default client User-Agents outright.
_USER_AGENT: Final = "Mozilla/5.0 (compatible; VocablyBot/1.0; +https://vocably.ir)"

#: Cap on senses parsed out of a response. "run" returns 63; nothing downstream
#: can use that many, and the rest would only be memory and, eventually, tokens.
_MAX_SENSES: Final = 25


class FreeDictionaryService(DictionaryService):
    def __init__(
        self,
        client: httpx.AsyncClient,
        cache: DictionaryCache | None = None,
        *,
        timeout_seconds: float = 1.0,
    ) -> None:
        self._client = client
        self._cache = cache
        # Deliberately far tighter than any AI timeout. This call sits in front
        # of a path that already works without it, so the correct behaviour when
        # it is slow is to give up immediately and generate. p95 is 325 ms, so
        # one second is ~3x headroom and still invisible next to a ~1.7 s model
        # call.
        self._timeout = timeout_seconds

    async def aclose(self) -> None:
        """Release the HTTP and Redis connections this adapter holds.

        Needed because a Celery task runs on a *fresh event loop* every time
        (see ``app.tasks.runtime``) while this adapter is memoized per process.
        Both pools bind to the loop that opened them, so one that outlives its
        loop hands the next task a dead connection — which surfaces as a
        ``RuntimeError`` and, because every call here is best-effort, silently
        degrades to "no cache" and "no dictionary" rather than to an error.
        """
        try:
            await self._client.aclose()
        except Exception as exc:  # noqa: BLE001 — teardown must not fail a task
            logger.info("dictionary client close failed: %s", type(exc).__name__)
        if self._cache is not None:
            await self._cache.aclose()

    async def look_up(self, term: str) -> DictionaryEntry | None:
        normalized = term.strip().lower()
        if not normalized:
            return None

        if self._cache is not None:
            cached = await self._cache.get(normalized)
            if cached is not None:
                # A cached miss is still an answer: negative caching is what
                # stops every typo a learner makes from becoming an upstream
                # request.
                return cached.entry

        entry = await self._fetch(normalized)
        if self._cache is not None:
            await self._cache.set(normalized, entry)
        return entry

    async def _fetch(self, term: str) -> DictionaryEntry | None:
        url = _BASE + quote(term)
        try:
            response = await self._client.get(
                url,
                timeout=self._timeout,
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            # Timeout, DNS, connection reset — all mean "no grounding", never an
            # error the learner should see.
            logger.info("dictionary lookup failed: %s", type(exc).__name__)
            return None

        if response.status_code == 404:
            return None
        if response.status_code != 200:
            # 403 (User-Agent), 429 (rate limit), 502 (upstream) all land here.
            # Logged at warning because a sustained 429 is worth noticing: it
            # means the cache is not doing its job.
            logger.warning("dictionary lookup returned HTTP %s", response.status_code)
            return None

        try:
            return self._parse(term, response.json())
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("dictionary payload unparsable: %s", type(exc).__name__)
            return None

    @staticmethod
    def _parse(term: str, payload: Any) -> DictionaryEntry | None:
        if not isinstance(payload, list) or not payload:
            return None
        senses: list[DictionarySense] = []
        phonetic = ""
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            phonetic = phonetic or _phonetic_of(entry)
            for meaning in entry.get("meanings") or []:
                part_of_speech = str(meaning.get("partOfSpeech") or "")
                for definition in meaning.get("definitions") or []:
                    text = str(definition.get("definition") or "").strip()
                    if not text or len(senses) >= _MAX_SENSES:
                        continue
                    senses.append(
                        DictionarySense(
                            definition=text,
                            part_of_speech=part_of_speech,
                            example=str(definition.get("example") or "").strip(),
                        )
                    )
        if not senses:
            return None
        return DictionaryEntry(term=term, senses=senses, phonetic=phonetic)


def _phonetic_of(entry: dict[str, Any]) -> str:
    """Pull an IPA transcription out of one dictionary entry.

    The API exposes pronunciation twice and inconsistently: a top-level
    ``phonetic`` string, and a ``phonetics`` array of regional variants each with
    its own ``text``. **Neither is reliably present.** Measured over a sample of
    common words, ``phonetic`` was empty for "keen" and "run" while ``phonetics``
    carried ``/kiːn/`` and ``/ɹʌn/`` — so reading only the top-level field drops
    the pronunciation for roughly one word in six, silently.

    Prefers the top-level value when there is one (it is the entry's own
    canonical choice) and otherwise takes the first non-empty variant. No attempt
    is made to pick a regional accent: the array is ordered by the source and
    carries no reliable locale marker, so choosing between ``/ˈhɑːdˌʃɪp/`` and
    ``/ˈhɑɹdˌʃɪp/`` would be guesswork dressed as a decision.
    """
    top = str(entry.get("phonetic") or "").strip()
    if top:
        return top
    for variant in entry.get("phonetics") or []:
        if not isinstance(variant, dict):
            continue
        text = str(variant.get("text") or "").strip()
        if text:
            return text
    return ""


class DictionaryCache:
    """Redis cache of dictionary entries, including misses.

    Separate from the AI lookup cache on purpose. That one is keyed by
    ``PROMPT_VERSION`` because its contents are model output that a prompt change
    invalidates. Wiktionary's entry for "undermine" does not change when we edit
    a prompt, so keying it the same way would throw away a perfectly good corpus
    on every prompt tweak.
    """

    def __init__(
        self,
        redis: Any,
        *,
        hit_ttl_seconds: int = 30 * 24 * 3600,
        miss_ttl_seconds: int = 24 * 3600,
        namespace: str = "dict:en:v1",
    ) -> None:
        self._redis = redis
        self._hit_ttl = hit_ttl_seconds
        # Misses expire far sooner than hits: a word absent today (new slang, a
        # word Wiktionary has not covered yet) may be present next month, and a
        # 30-day negative cache would hide that.
        self._miss_ttl = miss_ttl_seconds
        self._namespace = namespace

    def _key(self, term: str) -> str:
        return f"{self._namespace}:{term}"

    async def aclose(self) -> None:
        """Close the Redis pool. See :meth:`FreeDictionaryService.aclose`."""
        closer = getattr(self._redis, "aclose", None) or getattr(self._redis, "close", None)
        if closer is None:  # pragma: no cover — every client has one of the two
            return
        try:
            await closer()
        except Exception as exc:  # noqa: BLE001 — teardown must not fail a task
            logger.info("dictionary cache close failed: %s", type(exc).__name__)

    async def get(self, term: str) -> _CachedEntry | None:
        try:
            raw = await self._redis.get(self._key(term))
        except Exception as exc:  # noqa: BLE001 — cache faults are never fatal
            logger.info("dictionary cache read failed: %s", type(exc).__name__)
            return None
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except ValueError:
            return None
        if payload is None:
            return _CachedEntry(entry=None)
        return _CachedEntry(
            entry=DictionaryEntry(
                term=payload["term"],
                phonetic=payload.get("phonetic", ""),
                senses=[DictionarySense(**s) for s in payload.get("senses", [])],
            )
        )

    async def set(self, term: str, entry: DictionaryEntry | None) -> None:
        if entry is None:
            payload, ttl = "null", self._miss_ttl
        else:
            payload = json.dumps(
                {
                    "term": entry.term,
                    "phonetic": entry.phonetic,
                    "senses": [
                        {
                            "definition": s.definition,
                            "part_of_speech": s.part_of_speech,
                            "example": s.example,
                        }
                        for s in entry.senses
                    ],
                },
                ensure_ascii=False,
            )
            ttl = self._hit_ttl
        try:
            await self._redis.set(self._key(term), payload, ex=ttl)
        except Exception as exc:  # noqa: BLE001
            logger.info("dictionary cache write failed: %s", type(exc).__name__)


class _CachedEntry:
    """Wrapper that distinguishes "cached miss" from "not cached"."""

    __slots__ = ("entry",)

    def __init__(self, entry: DictionaryEntry | None) -> None:
        self.entry = entry
