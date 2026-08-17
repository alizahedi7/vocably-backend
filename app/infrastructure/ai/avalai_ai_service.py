"""Back-compatible import path for the AvalAI adapter.

The adapter was never AvalAI-specific — it drives the OpenAI Chat Completions
protocol against a configurable base URL — so it now lives in
:mod:`app.infrastructure.ai.openai_compatible_ai_service` and AvalAI is one of
several gateways in :mod:`app.infrastructure.ai.providers`.

This module stays because the name is referenced from outside the package: the
stdlib-only scripts in ``benchmarks/`` copy prompts and request-building from it
by path, and its unit tests are the ones that pin the OpenAI-shaped guardrails.
Nothing new should import from here.
"""

from __future__ import annotations

from app.infrastructure.ai.openai_compatible_ai_service import OpenAICompatibleAIService
from app.infrastructure.ai.providers import AvalAIProvider

#: Historical name. ``AvalAIProvider`` is the same class with ``name="avalai"``.
AvalAIService = AvalAIProvider

__all__ = ["AvalAIProvider", "AvalAIService", "OpenAICompatibleAIService"]
