"""Test-only process environment isolation.

This module is imported by the application configuration and dotenv loader.  It
activates only for ``python -m unittest`` after unittest itself is loaded, so it
cannot alter normal API, worker, CLI, or production startup behavior.
"""

from __future__ import annotations

import os
import sys
import unittest
from collections.abc import Callable
from typing import Any

_PREFIXES = (
    "AI_", "APP_", "AUTH_", "BUILD_", "CONTENT_", "CORS_", "DATABASE_",
    "DEVELOPMENT_", "DRIVE_", "DYNAMIC_", "ELASTICSEARCH_", "EXTERNAL_",
    "GEMINI_", "GOOGLE_", "HEALTHCHECK_", "INCREMENTAL_", "MANAGED_",
    "MICROSOFT_", "OAUTH_", "OPENAI_", "PROCESSING_", "PROXY_", "PUBLIC_",
    "RETENTION_", "SEARCH_", "SENSITIVE_", "TRUSTED_", "UNIFIED_", "WORKER_",
)
_EXACT_NAMES = {
    "API_DOCS_ENABLED", "APP_ENV", "ENVIRONMENT", "PERSISTENT_AUTH_ENABLED", "TESTING",
}
_SAFE_DEFAULTS = {"APP_ENV": "test", "ENVIRONMENT": "test", "TESTING": "true"}


def is_unittest_runtime() -> bool:
    return any("unittest" in argument for argument in sys.argv)


def reset_test_environment() -> None:
    """Remove ambient application settings and install deterministic test values."""
    for name in tuple(os.environ):
        if name in _EXACT_NAMES or name.startswith(_PREFIXES):
            os.environ.pop(name, None)
    os.environ.update(_SAFE_DEFAULTS)


def _clear_settings_cache() -> None:
    config_module = sys.modules.get("app.core.config")
    if config_module is not None:
        config_module.get_settings.cache_clear()


def _install_per_test_reset() -> None:
    if getattr(unittest.TestCase, "_cam_test_environment_wrapped", False):
        return
    original_run: Callable[..., Any] = unittest.TestCase.run

    def run_with_isolated_environment(self: unittest.TestCase, result: Any = None) -> Any:
        reset_test_environment()
        _clear_settings_cache()
        try:
            return original_run(self, result)
        finally:
            reset_test_environment()
            _clear_settings_cache()

    unittest.TestCase.run = run_with_isolated_environment
    unittest.TestCase._cam_test_environment_wrapped = True


def activate_test_environment() -> None:
    if not is_unittest_runtime():
        return
    reset_test_environment()
    _install_per_test_reset()
