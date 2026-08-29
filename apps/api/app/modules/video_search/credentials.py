"""Video-only Gemini credentials.

Video analysis must use its own Google project/API key so Free Tier quota is
accounted independently from Creative Image analysis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.ai_operations.credentials import (
    CreativeAiCredentialRepository,
    CreativeCredentialError,
    creative_credential_cipher,
)


class VideoGeminiCredentialError(RuntimeError):
    """Raised when the encrypted Video AI credential cannot be resolved."""


@dataclass(frozen=True, slots=True)
class VideoGeminiCredential:
    secret: str


class VideoGeminiCredentialResolver:
    def __init__(self, session_factory: Callable[[], Session], settings: Settings):
        self.session_factory, self.settings = session_factory, settings

    def resolve(self, tenant_id: str) -> VideoGeminiCredential:
        try:
            cipher = creative_credential_cipher(self.settings)
            with self.session_factory() as session:
                credential = CreativeAiCredentialRepository(
                    session, cipher
                ).get_active_secret(tenant_id, provider="gemini_video")
        except CreativeCredentialError as exc:
            raise VideoGeminiCredentialError(str(exc)) from exc
        if credential is None:
            raise VideoGeminiCredentialError("video_gemini_credential_unavailable")
