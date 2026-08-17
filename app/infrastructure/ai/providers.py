"""The OpenAI-protocol gateways this app can be pointed at.

Each class is :class:`OpenAICompatibleAIService` plus an identity: a name that
reaches logs, cache provenance and the failover chain's configuration, and the
URL to use when none is configured. There is deliberately no per-gateway
transport code — they speak one protocol, and a guardrail that lives in three
places is a guardrail fixed in one of them.

A class rather than a config row because identity is not the only thing that
ever differs: OpenRouter wants attribution headers, and the next gateway will
want something else. This is where such a quirk goes, and it stays one override
rather than a fork of the adapter.

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


class OpenRouterProvider(OpenAICompatibleAIService):
    """OpenRouter (https://openrouter.ai), a multi-upstream OpenAI-protocol router.

    Model ids are namespaced by upstream vendor — ``google/gemini-3.5-flash-lite``,
    not ``gemini-3.5-flash-lite`` — so a model name that works on the other two
    gateways will 404 here. This is the one place a fallback's ``*_MODEL`` cannot
    simply be copied from the primary's.
    """

    name = "openrouter"
    default_base_url = "https://openrouter.ai/api/v1"


#: Name → class, the only mapping between configuration and code. A name absent
#: from here is rejected at startup by ``Settings._validate_ai_provider`` rather
#: than failing on the first lookup that needed it.
PROVIDERS: dict[str, type[OpenAICompatibleAIService]] = {
    AvalAIProvider.name: AvalAIProvider,
    GapGPTProvider.name: GapGPTProvider,
    OpenRouterProvider.name: OpenRouterProvider,
}
