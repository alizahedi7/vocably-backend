"""Every production command reaches its process as one line.

In a YAML ``>`` block, a line indented deeper than the first keeps its newline.
The api command was written that way, so ``sh -c`` ran ``uvicorn … --port 8000``
alone and never reached the next line: the proxy-header, keep-alive and
shutdown flags were silently dropped, and every client shared Caddy's address.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "deploy" / "docker-compose.prod.yml"
SERVICES = yaml.safe_load(COMPOSE.read_text())["services"]


@pytest.mark.parametrize(
    "name", [n for n, s in SERVICES.items() if isinstance(s.get("command"), str)]
)
def test_the_command_is_one_line(name: str) -> None:
    assert "\n" not in SERVICES[name]["command"].strip()


def test_the_api_trusts_the_proxy_and_outlives_its_keep_alive() -> None:
    command = SERVICES["api"]["command"]
    assert "--forwarded-allow-ips='*'" in command
    assert "--timeout-keep-alive 130" in command
