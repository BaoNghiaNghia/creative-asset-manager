from __future__ import annotations

from app.core.config import Settings
from app.modules.visual_search.service import (
    VisualSearchDisabledError,
    VisualSearchOperationDisabledError,
    VisualSearchService,
)


class ExplodingProvider:
    def get_encoder(self):
        raise AssertionError("encoder must not be resolved while visual search is disabled")


def test_visual_search_flags_default_to_off() -> None:
    settings = Settings()
    service = VisualSearchService(settings, ExplodingProvider())
    assert service.capabilities().enabled is False
    assert service.capabilities().upload_enabled is False
    assert service.capabilities().crop_enabled is False
    assert service.capabilities().hybrid_text_enabled is False
    assert service.capabilities().backfill_enabled is False
    try:
        service.encoder_descriptor()
    except VisualSearchDisabledError:
        pass
    else:
        raise AssertionError("disabled visual search must not resolve an encoder")


def test_child_flags_require_global_visual_search_flag() -> None:
    service = VisualSearchService(Settings(VISUAL_SEARCH_UPLOAD_ENABLED=True))
    assert service.capabilities().upload_enabled is False
    try:
        service.require_operation("upload")
    except VisualSearchDisabledError:
        pass
    else:
        raise AssertionError("global visual flag must gate upload")


def test_operation_flag_is_enforced_after_global_enablement() -> None:
    service = VisualSearchService(Settings(VISUAL_SEARCH_ENABLED=True))
    assert service.capabilities().enabled is True
    try:
        service.require_operation("crop")
    except VisualSearchOperationDisabledError:
        pass
    else:
        raise AssertionError("crop must remain disabled independently")
