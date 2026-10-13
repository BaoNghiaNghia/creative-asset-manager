from types import SimpleNamespace

from app.modules.realistic_review_ugc import router as rrugc_router


def test_media_storage_status_warns_when_managed_drive_disconnected(monkeypatch):
    monkeypatch.setattr("app.modules.storage.managed_oauth.managed_storage_oauth_status",
        lambda _settings: {"root_folder_configured": True, "connected": False,
                           "reconnect_required": True, "account_email": "private@example.com",
                           "refresh_token": "never-expose"})
    result = rrugc_router.rrugc_media_storage_status(
        principal=SimpleNamespace(active_tenant_id="tenant-a"))
    assert result == {
        "status": "reconnect_required",
        "root_folder_configured": True,
        "connected": False,
        "reconnect_required": True,
        "retry_after_seconds": 60,
    }
    assert "account_email" not in result and "refresh_token" not in result


def test_media_storage_status_ready_only_with_active_connection(monkeypatch):
    monkeypatch.setattr("app.modules.storage.managed_oauth.managed_storage_oauth_status",
        lambda _settings: {"root_folder_configured": True, "connected": True,
                           "reconnect_required": False})
    result = rrugc_router.rrugc_media_storage_status(
        principal=SimpleNamespace(active_tenant_id="tenant-a"))
    assert result["status"] == "connected"
    assert result["connected"] is True
    assert result["reconnect_required"] is False
