"""The request's connection goes back to the pool before every provider call."""

from __future__ import annotations

from app.infrastructure.ai.connection_releasing_ai_service import ConnectionReleasingAIService


class _Provider:
    name = "stub"

    def __init__(self, log: list[str]) -> None:
        self._log = log

    async def look_up_meanings(self, term: str) -> str:
        self._log.append(f"call:{term}")
        return term.upper()

    async def some_method_added_later(self) -> str:
        self._log.append("call:later")
        return "ok"


async def test_releases_before_every_coroutine_method_and_passes_the_rest_through() -> None:
    log: list[str] = []

    async def release() -> None:
        log.append("release")

    wrapped = ConnectionReleasingAIService(_Provider(log), release)

    assert await wrapped.look_up_meanings("run") == "RUN"
    # Not a hand-written list: a method the port gains later is covered too.
    assert await wrapped.some_method_added_later() == "ok"
    assert wrapped.name == "stub"
    assert log == ["release", "call:run", "release", "call:later"]
