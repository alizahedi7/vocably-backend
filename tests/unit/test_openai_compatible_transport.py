"""The two shapes of one transport: Chat Completions and the Responses API."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.application.ports.ai_service import LearnerContext, LookupStatus
from app.infrastructure.ai.providers import AgentRouterProvider, AvalAIProvider

_CARD = {
    "status": "ok",
    "term": "gullible",
    "notice": None,
    "senses": [
        {
            "native_meaning": "زودباور",
            "definition": "easily tricked because you believe what people say",
            "example": "He was gullible enough to believe the email.",
            "context": "General",
            "part_of_speech": "adjective",
        }
    ],
}


class _FakeClient:
    """Records which endpoint was called and with what, answering a fixed card."""

    def __init__(self, *, status: str = "completed") -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._status = status

        async def chat_create(**kwargs: Any) -> Any:
            self.calls.append(("chat", kwargs))
            message = SimpleNamespace(content=json.dumps(_CARD))
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=message)])

        async def responses_create(**kwargs: Any) -> Any:
            self.calls.append(("responses", kwargs))
            return SimpleNamespace(
                status=self._status,
                incomplete_details=None,
                output_text=json.dumps(_CARD) if self._status == "completed" else "",
            )

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=chat_create))
        self.responses = SimpleNamespace(create=responses_create)


@pytest.mark.asyncio
async def test_agentrouter_goes_through_the_responses_api_with_the_schema() -> None:
    client = _FakeClient()
    provider = AgentRouterProvider(api_key="k", model="gpt-5.6-sol", client=client)  # type: ignore[arg-type]

    result = await provider.look_up_meanings("gullible", LearnerContext(native_language="Persian"))

    assert result.status is LookupStatus.OK
    assert [s.native_meaning for s in result.suggestions] == ["زودباور"]
    assert result.provider == "agentrouter" and result.model == "gpt-5.6-sol"
    (endpoint, kwargs), *rest = client.calls
    assert endpoint == "responses" and not rest
    assert kwargs["text"]["format"]["type"] == "json_schema"
    assert kwargs["text"]["format"]["strict"] is True
    assert kwargs["reasoning"] == {"effort": "low"}
    assert "instructions" in kwargs and kwargs["input"]


@pytest.mark.asyncio
async def test_a_chat_completions_gateway_is_untouched() -> None:
    client = _FakeClient()
    provider = AvalAIProvider(api_key="k", model="m", client=client)  # type: ignore[arg-type]

    result = await provider.look_up_meanings("gullible", LearnerContext(native_language="Persian"))

    assert result.suggestions and client.calls[0][0] == "chat"
    assert client.calls[0][1]["response_format"]["type"] == "json_schema"


@pytest.mark.asyncio
async def test_a_budget_spent_on_reasoning_is_retried_once_then_fails_closed() -> None:
    from app.core.exceptions import ExternalServiceError

    client = _FakeClient(status="incomplete")
    provider = AgentRouterProvider(api_key="k", model="gpt-6-astra", client=client)  # type: ignore[arg-type]

    with pytest.raises(ExternalServiceError):
        await provider.look_up_meanings("gullible", LearnerContext(native_language="Persian"))

    assert len(client.calls) == 2
    # An empty answer is not an off-schema answer: the retry still asks for the schema.
    assert all("text" in kwargs for _, kwargs in client.calls)
