"""The access log is silenced unless ``ACCESS_LOG`` asks for it."""

from __future__ import annotations

import logging

import pytest

from app.core import logging as app_logging
from app.core.config import settings


@pytest.mark.parametrize(("enabled", "emits"), [(False, False), (True, True)])
def test_access_log_follows_the_setting(
    enabled: bool, emits: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    access = logging.getLogger("uvicorn.access")
    monkeypatch.setattr(access, "level", logging.NOTSET)
    monkeypatch.setattr(settings, "access_log", enabled)
    monkeypatch.setattr(app_logging, "_CONFIGURED", False)
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        app_logging.configure_logging()
        assert access.isEnabledFor(logging.INFO) is emits
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
