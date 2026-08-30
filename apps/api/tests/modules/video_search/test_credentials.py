from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.modules.video_search.credentials import (
    VideoGeminiCredentialResolver,
)


def test_resolver_returns_decrypted_video_key_and_fingerprint():
    stored = SimpleNamespace(secret="video-key", fingerprint="a" * 64)
    repository = MagicMock()
    repository.get_active_secret.return_value = stored
    session = MagicMock()
    session.__enter__.return_value = session

    with (
        patch(
            "app.modules.video_search.credentials.creative_credential_cipher",
            return_value=MagicMock(),
        ),
        patch(
            "app.modules.video_search.credentials.CreativeAiCredentialRepository",
            return_value=repository,
        ),
    ):
        credential = VideoGeminiCredentialResolver(
            lambda: session, MagicMock()
        ).resolve("tenant-a")

    assert credential.secret == "video-key"
    assert credential.fingerprint == "a" * 64
    repository.get_active_secret.assert_called_once_with(
        "tenant-a", provider="gemini_video"
    )
