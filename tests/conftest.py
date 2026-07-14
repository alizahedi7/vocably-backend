"""Root conftest: isolate the test suite from the developer's local ``.env``.

``app.core.config.settings`` is a module-level singleton built at import time, and
``tests/api/conftest.py`` imports ``app.main`` (which imports settings transitively)
as soon as pytest collects it. Point ``Settings`` at a nonexistent env file *before*
that first import, or a local ``.env`` used for manual testing (e.g.
``GOOGLE_VERIFIER=google``) silently changes behavior for the whole suite.

Pytest always imports a directory's conftest.py before its test modules, and parent
conftests before child ones, so this file runs first for every test under ``tests/``.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENV_FILE", os.devnull)
# The class default ("change-me") is below PyJWT's recommended HMAC key length and
# triggers a warning on every token issued; tests don't care what the value is.
os.environ.setdefault("SECRET_KEY", "test-only-secret-key-not-for-production-use-0123456789")
