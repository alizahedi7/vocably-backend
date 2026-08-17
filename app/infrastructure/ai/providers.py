"""The OpenAI-protocol gateways this app can be pointed at.

Each class is :class:`OpenAICompatibleAIService` plus an identity: a name that
reaches logs, cache provenance and the failover chain's configuration, and the
URL to use when none is configured, plus any header it gates on. There is
deliberately no per-gateway transport code — they speak one protocol, and a
guardrail that lives in four places is a guardrail fixed in one of them.

**Two of these exist for the deck-build pipeline, not for learner lookups.**
``tabitoken`` and ``agentrouter`` catalogue frontier Claude models and nothing
cheap. A published course deck is written once and read by everyone, so it is
worth the better model; an interactive lookup, waited on behind a spinner and
repeated all day, is not. ``AI_BUILD_PROVIDER`` selects them — see
:func:`app.infrastructure.ai.factory.build_ai_provider`.

A class rather than a config row because identity is not the only thing that
ever differs: both Claude proxies gate on an exact ``user-agent``, and the next
gateway will want something else. This is where such a quirk goes, and it stays
one override rather than a fork of the adapter.

Registered in :data:`PROVIDERS`, which
:func:`app.infrastructure.ai.factory.provider_for` reads. Adding a gateway is a
class here, an entry there, and a settings block — no change to the chain, the
decorators, or the failover wrapper.
"""

from __future__ import annotations

from app.infrastructure.ai.openai_compatible_ai_service import OpenAICompatibleAIService


class AvalAIProvider(OpenAICompatibleAIService):
    """AvalAI (https://avalai.ir), an Iran-based OpenAI-protocol gateway."""

    name = "avalai"
    default_base_url = "https://api.avalai.ir/v1"


class GapGPTProvider(OpenAICompatibleAIService):
    """GapGPT (https://gapgpt.app), likewise OpenAI-protocol.

    Note for operators, established by measurement rather than documentation:
    GapGPT **pre-authorises ``max_tokens`` × price before running the request**.
    At a low balance a call with ``max_tokens=4096`` is refused outright with a
    403 naming the shortfall, while the same call with ``max_tokens=10``
    succeeds — so an underfunded account fails every real lookup while looking
    reachable and correctly configured. Balance must stay well above
    ``max_tokens`` × price, not merely above zero.
    """

    name = "gapgpt"
    default_base_url = "https://api.gapgpt.app/v1"


#: The exact client string tabitoken and agentrouter gate on. Without it both
#: answer ``401 unauthorized client detected`` however valid the key is, and the
#: SDK's ``default_headers`` cannot deliver it — see ``_forced_header_client``.
_CLAUDE_CLI_UA = {"user-agent": "claude-cli/2.1.0 (external, cli)"}


class TabiTokenProvider(OpenAICompatibleAIService):
    """tabitoken (https://tabitoken.com), a Claude proxy.

    Catalogues frontier models only (``claude-opus-5``, ``claude-opus-4-8``)
    rather than the cheap fast models the request path wants — which is why it
    belongs on ``AI_BUILD_PROVIDER`` and not on ``AI_PROVIDER``.

    It **accepts ``response_format`` and then ignores it**, answering 200 with
    prose. That is one of the two failure modes ``_complete`` latches on: the
    Pydantic validation fails, structured output is switched off for the
    process, and the retry goes out with the schema stated in the prompt.
    """

    name = "tabitoken"
    default_base_url = "https://tabitoken.com/v1"
    default_extra_headers = _CLAUDE_CLI_UA


class AgentRouterProvider(OpenAICompatibleAIService):
    """agentrouter (https://agentrouter.org), likewise a Claude proxy.

    Speaks both protocols; this drives the OpenAI one, since that is where the
    provider fleet and its failover live. ``ANTHROPIC_BASE_URL`` can still point
    the Anthropic adapter at the same host for the request path.

    It **rejects ``response_format``** outright with a 400, which the SDK raises
    as ``BadRequestError`` — the other latch in ``_request``, which falls back to
    prompt-enforced JSON and retries once.
    """

    name = "agentrouter"
    default_base_url = "https://agentrouter.org/v1"
    default_extra_headers = _CLAUDE_CLI_UA


#: Name → class, the only mapping between configuration and code. A name absent
#: from here is rejected at startup by ``Settings._validate_ai_provider`` rather
#: than failing on the first lookup that needed it.
PROVIDERS: dict[str, type[OpenAICompatibleAIService]] = {
    AvalAIProvider.name: AvalAIProvider,
    GapGPTProvider.name: GapGPTProvider,
    TabiTokenProvider.name: TabiTokenProvider,
    AgentRouterProvider.name: AgentRouterProvider,
}
