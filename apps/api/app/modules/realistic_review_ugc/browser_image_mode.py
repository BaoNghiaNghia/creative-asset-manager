"""Stage 1 browser image engine rollout guard.

Browser Extension must never become an accidental production fallback:
the Windows mock bridge is not an authorized live generation provider.
The next phase can replace this capability gate only after real website
authorization, end-to-end output QA, and durable CAM↔Windows acknowledgements.
"""
from __future__ import annotations

from typing import Literal

ImageEngine = Literal["codex_cli", "browser_extension"]
DEFAULT_IMAGE_ENGINE: ImageEngine = "codex_cli"
BROWSER_EXTENSION_READY = False
BROWSER_EXTENSION_STATUS = "awaiting_live_provider_validation"


class BrowserImageModeUnavailable(ValueError):
    pass


def selectable_modes() -> dict:
    return {
        "default_engine": DEFAULT_IMAGE_ENGINE,
        "modes": [
            {"id": "codex_cli", "label": "Codex CLI", "ready": True,
             "description": "Production default. Existing jobs are unaffected."},
            {"id": "browser_extension", "label": "Browser Extension", "ready": False,
             "description": "Prototype testing only; production routing is locked.",
             "status": BROWSER_EXTENSION_STATUS},
        ],
    }


def require_ready_engine(image_engine: str) -> ImageEngine:
    if image_engine == DEFAULT_IMAGE_ENGINE:
        return DEFAULT_IMAGE_ENGINE
    if image_engine == "browser_extension":
        raise BrowserImageModeUnavailable(
            "Browser Extension is still under reference-upload and real-provider QA. "
            "Codex CLI remains the active engine. No browser job was queued."
        )
    raise ValueError("Unknown image generation engine.")
