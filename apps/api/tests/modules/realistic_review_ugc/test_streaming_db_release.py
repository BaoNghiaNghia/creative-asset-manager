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
            session=session,
            principal=_principal(),
        )
    )

    assert session.closed is True
    assert storage.opened_after_close is True
    assert response.headers["etag"] == '"abc123"'
