"""The dictionary adapter: parsing, and the many ways the free API says no.

Every failure mode here was observed against the live endpoint, not imagined:
403 for a default User-Agent, 429 under a burst, intermittent 502s, and 404 for
a third of real learner input. All of them must reduce to ``None``, because the
caller's only question is "is there grounding or not".
"""

from __future__ import annotations

import httpx
import pytest

from app.infrastructure.dictionary.free_dictionary_service import (
    DictionaryCache,
    FreeDictionaryService,
)

_PAYLOAD = [
    {
        "word": "undermine",
        "phonetic": "/ʌndəˈmaɪn/",
        "meanings": [
            {
                "partOfSpeech": "verb",
                "definitions": [
                    {
                        "definition": "To weaken or damage something gradually.",
                        "example": "His remarks undermined her authority.",
                    },
                    {"definition": "To dig beneath a structure."},
                ],
            }
        ],
    }
]


def _service(handler: object, **kwargs: object) -> FreeDictionaryService:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return FreeDictionaryService(httpx.AsyncClient(transport=transport), **kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_parses_senses_and_phonetic() -> None:
    entry = await _service(lambda r: httpx.Response(200, json=_PAYLOAD)).look_up("undermine")

    assert entry is not None
    assert entry.phonetic == "/ʌndəˈmaɪn/"
    assert len(entry.senses) == 2
    assert entry.senses[0].part_of_speech == "verb"
    assert entry.senses[0].example == "His remarks undermined her authority."
    # The second sense has no example; absence must be "" rather than a crash.
    assert entry.senses[1].example == ""


@pytest.mark.asyncio
async def test_sends_a_real_user_agent() -> None:
    """The endpoint 403s default client User-Agents, so this is load-bearing."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("user-agent", ""))
        return httpx.Response(200, json=_PAYLOAD)

    await _service(handler).look_up("undermine")

    assert seen and "python" not in seen[0].lower()
    assert "Vocably" in seen[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404, 429, 500, 502], ids=str)
async def test_every_error_status_is_a_miss_not_an_exception(status: int) -> None:
    entry = await _service(lambda r: httpx.Response(status, json={})).look_up("undermine")

    assert entry is None


@pytest.mark.asyncio
async def test_timeout_is_a_miss() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    assert await _service(handler).look_up("undermine") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [{}, [], [{"word": "x"}], [{"word": "x", "meanings": [{"definitions": []}]}]],
    ids=["object", "empty-list", "no-meanings", "no-definitions"],
)
async def test_payloads_without_usable_senses_are_a_miss(body: object) -> None:
    """An entry with no definitions is grounding we cannot use."""
    assert await _service(lambda r: httpx.Response(200, json=body)).look_up("x") is None


@pytest.mark.asyncio
async def test_blank_input_never_calls_the_api() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=_PAYLOAD)

    assert await _service(handler).look_up("   ") is None
    assert calls == []


@pytest.mark.asyncio
async def test_term_is_normalized_and_url_encoded() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=_PAYLOAD)

    await _service(handler).look_up("  Put Off  ")

    assert seen[0].endswith("/put%20off")


# ── Cache ─────────────────────────────────────────────────────


class _FakeRedis:
    """Enough of redis.asyncio for the cache, plus a switch to make it fail."""

    def __init__(self, broken: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.broken = broken

    async def get(self, key: str) -> str | None:
        if self.broken:
            raise ConnectionError("redis down")
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        if self.broken:
            raise ConnectionError("redis down")
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex


@pytest.mark.asyncio
async def test_second_lookup_is_served_from_cache() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=_PAYLOAD)

    redis = _FakeRedis()
    service = _service(handler, cache=DictionaryCache(redis))

    first = await service.look_up("undermine")
    second = await service.look_up("undermine")

    assert len(calls) == 1, "the second lookup must not reach the rate-limited API"
    assert second is not None
    assert first is not None
    assert [s.definition for s in second.senses] == [s.definition for s in first.senses]
    assert second.phonetic == first.phonetic


@pytest.mark.asyncio
async def test_misses_are_cached_too_and_expire_sooner() -> None:
    """Negative caching is what stops every typo becoming an upstream request."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(404, json={})

    redis = _FakeRedis()
    cache = DictionaryCache(redis, hit_ttl_seconds=3000, miss_ttl_seconds=60)
    service = _service(handler, cache=cache)

    assert await service.look_up("recieve") is None
    assert await service.look_up("recieve") is None

    assert len(calls) == 1
    assert list(redis.ttls.values()) == [60]


@pytest.mark.asyncio
async def test_a_broken_cache_degrades_to_calling_the_api() -> None:
    """A cache fault must never turn a working lookup into a failure."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=_PAYLOAD)

    service = _service(handler, cache=DictionaryCache(_FakeRedis(broken=True)))

    entry = await service.look_up("undermine")

    assert entry is not None
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_cache_namespace_is_independent_of_prompt_version() -> None:
    """Wiktionary's entry does not change when we edit a prompt."""
    redis = _FakeRedis()
    await DictionaryCache(redis).set("undermine", None)

    assert list(redis.store) == ["dict:en:v1:undermine"]


# ── Phonetics ─────────────────────────────────────────────────


def _entry_with(phonetic: object, phonetics: object) -> list[dict[str, object]]:
    body: dict[str, object] = {
        "word": "keen",
        "meanings": [{"partOfSpeech": "adjective", "definitions": [{"definition": "eager"}]}],
    }
    if phonetic is not None:
        body["phonetic"] = phonetic
    if phonetics is not None:
        body["phonetics"] = phonetics
    return [body]


@pytest.mark.asyncio
async def test_prefers_the_top_level_phonetic() -> None:
    body = _entry_with("/kiːn/", [{"text": "/kin/"}])
    entry = await _service(lambda r: httpx.Response(200, json=body)).look_up("keen")

    assert entry is not None
    assert entry.phonetic == "/kiːn/"


@pytest.mark.asyncio
async def test_falls_back_to_the_phonetics_array() -> None:
    """Real behaviour: "keen" and "run" ship an empty top-level `phonetic`.

    Reading only that field drops the pronunciation for roughly one common word
    in six, and does it silently.
    """
    body = _entry_with("", [{"audio": "x.mp3"}, {"text": "/kiːn/"}, {"text": "/kin/"}])
    entry = await _service(lambda r: httpx.Response(200, json=body)).look_up("keen")

    assert entry is not None
    assert entry.phonetic == "/kiːn/"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phonetic", "phonetics"),
    [(None, None), ("", []), ("", [{"audio": "only-audio.mp3"}]), ("  ", [{"text": "  "}])],
    ids=["absent", "both-empty", "audio-only", "whitespace"],
)
async def test_missing_pronunciation_is_empty_not_an_error(
    phonetic: object, phonetics: object
) -> None:
    """A third of words have no IPA; that is a blank field, never a failure."""
    body = _entry_with(phonetic, phonetics)
    entry = await _service(lambda r: httpx.Response(200, json=body)).look_up("keen")

    assert entry is not None
    assert entry.phonetic == ""
    assert entry.senses, "a missing pronunciation must not discard the senses"
