"""Products Stage 2 must never download original Ecommerce bytes for thumbnail tiles."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.modules.authorization.principal import CurrentPrincipal
from app.modules.realistic_review_ugc import router as rrugc


def principal(tenant: str = "tenant-one") -> CurrentPrincipal:
    return CurrentPrincipal(
        user_id="user-one", active_tenant_id=tenant, membership_id="member",
        external_identity=None, effective_roles=frozenset({"operator"}),
        effective_permissions=frozenset({"realistic_review_ugc.read"}),
        platform_admin=False, session_id="session-one", authorization_source="test",
    )


class FakeSession:
    def __init__(self) -> None:
        self.closed = False
        self.queries = 0

    def scalar(self, _statement):
        self.queries += 1
        return SimpleNamespace(
            id="job-one", output_remote_file_id="stored-image-one",
            output_content_type="image/png", output_size_bytes=2_000_000,
            remote_file_id="stored-version-two", content_type="image/png",
            size_bytes=1_800_000,
        )

    def close(self) -> None:
        self.closed = True


def test_ecommerce_thumbnail_uses_managed_preview_and_releases_session(monkeypatch):
    session = FakeSession()
    storage = object()
    calls = []
    monkeypatch.setattr(rrugc, "build_managed_storage_provider", lambda _settings: storage)

    async def preview(passed_storage, **kwargs):
        assert session.closed
        assert passed_storage is storage
        calls.append(kwargs)
        return rrugc.Response(content=b"small-thumb", media_type="image/jpeg",
                              headers={"ETag": kwargs["etag"], "Cache-Control": kwargs["cache_control"]})

    monkeypatch.setattr(rrugc, "_managed_drive_thumbnail_response", preview)
    response = asyncio.run(rrugc.ecommerce_job_output(
        "job-one", version=None, thumbnail=True, size=128, v=None,
        if_none_match=None, session=session, principal=principal(),
    ))
    assert response.status_code == 200
    assert response.body == b"small-thumb"
    assert calls[0]["tenant_id"] == "tenant-one"
    assert calls[0]["remote_file_id"] == "stored-image-one"
    assert calls[0]["size_pixels"] == 128
    assert calls[0]["cache_version"] == "ecommerce:job-one:stored-image-one"
    assert response.headers["cache-control"].startswith("private")
    etag = response.headers["etag"]

    # Conditional requests are authorized by the tenant query but must not touch storage.
    second = asyncio.run(rrugc.ecommerce_job_output(
        "job-one", version=None, thumbnail=True, size=128, v=None,
        if_none_match=etag, session=FakeSession(), principal=principal(),
    ))
    assert second.status_code == 304
    assert len(calls) == 1


def test_ecommerce_saved_version_thumbnail_resolves_exact_version(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(rrugc, "build_managed_storage_provider", lambda _settings: object())
    remote_files = []

    async def preview(_storage, **kwargs):
        remote_files.append(kwargs["remote_file_id"])
        return rrugc.Response(content=b"thumb", media_type="image/jpeg")

    monkeypatch.setattr(rrugc, "_managed_drive_thumbnail_response", preview)
    response = asyncio.run(rrugc.ecommerce_job_output(
        "job-one", version=2, thumbnail=True, size=256, v=None,
        if_none_match=None, session=session, principal=principal(),
    ))
    assert response.body == b"thumb"
    assert session.queries == 2
    assert remote_files == ["stored-version-two"]


def test_ecommerce_thumbnail_missing_never_streams_original(monkeypatch):
    session = FakeSession()
    class Storage:
        async def open_asset(self, _input):
            raise AssertionError("Original bytes must never be streamed for thumbnail URLs")

    monkeypatch.setattr(rrugc, "build_managed_storage_provider", lambda _settings: Storage())

    async def missing(_storage, **_kwargs):
        return None

    monkeypatch.setattr(rrugc, "_managed_drive_thumbnail_response", missing)
    with pytest.raises(HTTPException) as error:
        asyncio.run(rrugc.ecommerce_job_output(
            "job-one", version=None, thumbnail=True, size=128, v=None,
            if_none_match=None, session=session, principal=principal(),
        ))
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "thumbnail_unavailable"
    assert session.closed
