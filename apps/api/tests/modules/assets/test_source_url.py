from app.modules.assets.source_url import resolve_source_web_url


def test_google_uses_stored_web_view_link_first():
    assert resolve_source_web_url(
        provider="google-drive",
        external_asset_id="file-123",
        source_metadata={"webViewLink": "https://drive.google.com/file/d/file-123/view"},
    ) == "https://drive.google.com/file/d/file-123/view"


def test_google_falls_back_to_safe_file_link():
    assert resolve_source_web_url(
        provider="google-drive", external_asset_id="a/b?c", source_metadata={}
    ) == "https://drive.google.com/open?id=a%2Fb%3Fc"


def test_sharepoint_requires_stored_allowed_link():
    assert resolve_source_web_url(
        provider="sharepoint",
        external_asset_id="item-1",
        source_metadata={"webUrl": "https://tenant.sharepoint.com/:f:/r/sites/design"},
    ) == "https://tenant.sharepoint.com/:f:/r/sites/design"
    assert resolve_source_web_url(
        provider="sharepoint", external_asset_id="item-1", source_metadata={}
    ) is None


def test_rejects_unsafe_or_wrong_provider_hosts():
    assert resolve_source_web_url(
        provider="google-drive",
        external_asset_id="file-1",
        source_metadata={"web_url": "javascript:alert(1)"},
    ) == "https://drive.google.com/open?id=file-1"
    assert resolve_source_web_url(
        provider="google-drive",
        external_asset_id="file-1",
        source_metadata={"web_url": "https://evil.example/file-1"},
    ) == "https://drive.google.com/open?id=file-1"
