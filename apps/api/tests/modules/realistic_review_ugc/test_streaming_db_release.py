from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.modules.authorization.principal import CurrentPrincipal
from app.modules.realistic_review_ugc import router as rrugc_router_module


def _principal() -> CurrentPrincipal:
    return CurrentPrincipal(
        user_id="user-a",
        active_tenant_id="tenant-a",
        membership_id="membership-a",
        external_identity=None,
        effective_roles=frozenset({"operator"}),
        effective_permissions=frozenset({"realistic_review_ugc.read"}),
        platform_admin=False,
        session_id="session-a",
        authorization_source="test",
    )


class _TrackingSession:
    def __init__(self) -> None:
        self.closed = False

    def scalar(self, _statement):
        return SimpleNamespace(
            id="plan-a",
            source_file_id="drive-file-a",
            source_mime_type="image/png",
            source_size_bytes=123,
            source_revision="abc123",
        )

    def close(self) -> None:
        self.closed = True


class _TrackingStorage:
    def __init__(self, session: _TrackingSession) -> None:
        self.session = session
        self.opened_after_close = False

    async def open_asset(self, _input):
        self.opened_after_close = self.session.closed

        async def body():
            yield b"image"

        async def close():
            return None

        return SimpleNamespace(
            body=body(),
            close=close,
            content_type="image/png",
        )


def test_source_plan_image_releases_db_before_remote_storage(monkeypatch):
    session = _TrackingSession()
    storage = _TrackingStorage(session)
    monkeypatch.setattr(
        rrugc_router_module,
        "build_managed_storage_provider",
        lambda _settings: storage,
    )

    response = asyncio.run(
        rrugc_router_module.get_source_plan_image(
            "plan-a",
            request=SimpleNamespace(
                headers={},
                app=SimpleNamespace(
                    state=SimpleNamespace(google_drive_stream_client=None)
                ),
            ),
            thumbnail=False,
            size=128,
            session=session,
            principal=_principal(),
        )
    )

    assert session.closed is True
    assert storage.opened_after_close is True
    assert response.headers["etag"] == '"abc123"'


def test_source_plan_thumbnail_conditional_etag_skips_remote_storage(monkeypatch):
    session = _TrackingSession()

    def fail_storage_build(_settings):
        raise AssertionError("304 thumbnail response must not touch remote storage")

    monkeypatch.setattr(
        rrugc_router_module,
        "build_managed_storage_provider",
        fail_storage_build,
    )

    response = asyncio.run(
        rrugc_router_module.get_source_plan_image(
            "plan-a",
            request=SimpleNamespace(
                headers={"if-none-match": '"abc123"'},
                app=SimpleNamespace(
                    state=SimpleNamespace(google_drive_stream_client=None)
                ),
            ),
            thumbnail=True,
            size=128,
            session=session,
            principal=_principal(),
        )
    )

    assert session.closed is True
    assert response.status_code == 304
    assert response.headers["etag"] == '"abc123"'
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"


def test_managed_drive_thumbnail_reuses_shared_byte_cache(monkeypatch):
    tenant_id = "tenant-thumbnail-cache-test"
    remote_file_id = "drive-thumbnail-cache-test"
    rrugc_router_module.thumbnail_cache.invalidate_where(
        lambda key: key[0] == tenant_id and key[1] == "rrugc-source-plan"
    )

    class ThumbnailStorage(rrugc_router_module.GoogleDriveAssetStorage):
        def __init__(self):
            pass

        async def get_access_token(self):
            return "test-access-token"

    class Upstream:
        headers = {
            "content-type": "image/webp",
            "last-modified": "Sun, 05 Oct 2026 04:00:00 GMT",
        }

        async def aiter_raw(self):
            yield b"thumbnail-bytes"

    calls = {"open": 0, "close": 0}
    shared_client = object()

    async def fake_open(*_args, **kwargs):
        calls["open"] += 1
        assert kwargs["http_client"] is shared_client
        assert kwargs["size_pixels"] == 128
        return shared_client, Upstream()

    async def fake_close(client, _upstream, close_client=True):
        calls["close"] += 1
        assert client is shared_client
        assert close_client is False

    monkeypatch.setattr(
        rrugc_router_module,
        "open_thumbnail_stream",
        fake_open,
    )
    monkeypatch.setattr(
        rrugc_router_module,
        "close_thumbnail_stream",
        fake_close,
    )

    async def scenario():
        kwargs = {
            "tenant_id": tenant_id,
            "remote_file_id": remote_file_id,
            "size_pixels": 128,
            "cache_control": "private, max-age=31536000, immutable",
            "cache_version": "revision-a",
            "etag": '"revision-a"',
            "http_client": shared_client,
        }
        first = await rrugc_router_module._managed_drive_thumbnail_response(
            ThumbnailStorage(),
            **kwargs,
        )
        second = await rrugc_router_module._managed_drive_thumbnail_response(
            ThumbnailStorage(),
            **kwargs,
        )
        return first, second

    first, second = asyncio.run(scenario())

    assert first is not None
    assert second is not None
    assert first.body == b"thumbnail-bytes"
    assert second.body == b"thumbnail-bytes"
    assert calls == {"open": 1, "close": 1}
