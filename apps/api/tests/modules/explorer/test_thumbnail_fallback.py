import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.modules.explorer.router import thumbnail
from app.providers.google.drive import GoogleDriveThumbnailUnavailable
from app.providers.microsoft.onedrive import OneDriveThumbnailUnavailable


def test_video_thumbnail_returns_safe_image_when_drive_has_no_poster() -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()), headers={}, query_params={})
    principal = SimpleNamespace(active_tenant_id="tenant-a")

    with patch(
        "app.modules.explorer.router._authorized_file_context",
        new=AsyncMock(return_value=("token", "tenant-a", "source-a")),
    ), patch(
        "app.modules.explorer.router.open_google_thumbnail",
        new=AsyncMock(side_effect=GoogleDriveThumbnailUnavailable("video-a")),
    ):
        response = asyncio.run(
            thumbnail(
                request=request,
                item_id="video-a",
                provider="google-drive",
                session=SimpleNamespace(close=lambda: None),
                principal=principal,
                external_source_id="source-a",
                fallback="video",
            )
        )

    assert response.status_code == 200
    assert response.media_type == "image/svg+xml"
    assert b"Video preview unavailable" in response.body
    assert b"token" not in response.body

def test_onedrive_video_thumbnail_returns_safe_image_when_graph_has_no_poster() -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()), headers={}, query_params={})
    principal = SimpleNamespace(active_tenant_id="tenant-a")

    with patch(
        "app.modules.explorer.router._authorized_file_context",
        new=AsyncMock(return_value=("token", "tenant-a", "source-a")),
    ), patch(
        "app.modules.explorer.router.open_onedrive_thumbnail",
        new=AsyncMock(side_effect=OneDriveThumbnailUnavailable("video-a")),
    ):
        response = asyncio.run(
            thumbnail(
                request=request,
                item_id="video-a",
                provider="onedrive",
                session=SimpleNamespace(close=lambda: None),
                principal=principal,
                external_source_id="source-a",
                fallback="video",
            )
        )

    assert response.status_code == 200
    assert response.media_type == "image/svg+xml"
    assert b"Video preview unavailable" in response.body
    assert b"token" not in response.body
