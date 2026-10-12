"""Stage 4 must fail before queuing if Managed Drive is not connected."""
from types import SimpleNamespace

import pytest

from app.modules.realistic_review_ugc import stage2
from app.providers.storage.unconfigured import UnconfiguredAssetStorageProvider


def test_production_missing_managed_drive_reports_actionable_error(monkeypatch):
    monkeypatch.setattr(
        stage2, "build_managed_storage_provider",
        lambda _settings: UnconfiguredAssetStorageProvider(),
    )
    with pytest.raises(stage2.RrugcStage2Error) as exc:
        stage2._require_stage4_storage(SimpleNamespace(is_production=True))
    assert exc.value.code == "stage4_managed_drive_not_connected"
    assert exc.value.status_code == 409
    assert "Managed Google Drive" in exc.value.message


def test_stage4_gate_respects_connected_provider(monkeypatch):
    provider = object()
    monkeypatch.setattr(stage2, "build_managed_storage_provider", lambda _settings: provider)
    assert stage2._require_stage4_storage(SimpleNamespace(is_production=True)) is None


def test_stage4_gate_does_not_require_production_oauth_in_tests(monkeypatch):
    def forbidden(_settings):
        raise AssertionError("Development tests should not reach production OAuth")
    monkeypatch.setattr(stage2, "build_managed_storage_provider", forbidden)
    assert stage2._require_stage4_storage(SimpleNamespace(is_production=False)) is None
