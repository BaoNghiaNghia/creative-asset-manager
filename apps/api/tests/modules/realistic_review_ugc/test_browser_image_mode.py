"""Browser Extension stays production-locked while mock worker is under test."""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.modules.realistic_review_ugc.browser_image_mode import (
    BrowserImageModeUnavailable, DEFAULT_IMAGE_ENGINE,
    require_ready_engine, selectable_modes,
)
from app.modules.realistic_review_ugc.creative_brief_router import (
    CreativeV3GenerateRequest, CreativeV3RegenerateRequest,
)
from app.modules.realistic_review_ugc.keyword_images import (
    KeywordImageError, KeywordImageService,
)


def test_codex_is_default_browser_has_no_automatic_fallback():
    assert DEFAULT_IMAGE_ENGINE == "codex_cli"
    assert require_ready_engine("codex_cli") == "codex_cli"
    modes = selectable_modes()
    assert modes["default_engine"] == "codex_cli"
    assert modes["modes"][0]["ready"] is True
    assert modes["modes"][1]["id"] == "browser_extension"
    assert modes["modes"][1]["ready"] is False
    with pytest.raises(BrowserImageModeUnavailable, match="No browser job"):
        require_ready_engine("browser_extension")
    with pytest.raises(ValueError, match="Unknown"):
        require_ready_engine("impossible")


def test_http_request_schema_is_backward_compatible_and_explicit():
    default = CreativeV3GenerateRequest(expected_brief_updated_at="2026-11-04T07:00:00Z")
    assert default.image_engine == "codex_cli"
    assert default.design_style == "auto"
    explicit = CreativeV3RegenerateRequest(
        expected_brief_updated_at="2026-11-04T07:00:00Z",
        image_engine="browser_extension",
        confirm_regenerate=True,
    )
    assert explicit.image_engine == "browser_extension"
    with pytest.raises(ValidationError):
        CreativeV3GenerateRequest(
            expected_brief_updated_at="2026-11-04T07:00:00Z",
            image_engine="unsafe",
        )


def test_extension_queue_rejected_before_any_database_access():
    service = KeywordImageService(session=None, settings=SimpleNamespace())
    with pytest.raises(KeywordImageError) as failure:
        service.queue_creative_v3(
            tenant_id="boostgame", user_id="u", keyword_id="never-used",
            expected_brief_updated_at="2026-11-04T07:00:00Z",
            image_engine="browser_extension",
        )
    assert failure.value.code == "browser_extension_not_verified"
    assert failure.value.status_code == 503
