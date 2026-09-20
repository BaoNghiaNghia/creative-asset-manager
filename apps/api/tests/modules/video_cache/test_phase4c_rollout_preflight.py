from __future__ import annotations

import json
from urllib.error import HTTPError

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.modules.auth_persistence.model import TenantModel
from app.modules.video_cache.model import (
    VIDEO_CDN_DELIVERY_SETTING_KEY,
    VideoCacheObjectModel,
    VideoDeliveryRuntimeSettingModel,
)
from app.modules.video_cache.service import video_cache_key
from app.operations.video_delivery_preflight import (
    probe_worker_ticket,
    video_delivery_preflight,
)

SECRET = "phase-4c-test-signing-secret-with-strong-entropy-2026"


def configured_settings(**updates) -> Settings:
    values = {
        "R2_VIDEO_CACHE_ENABLED": True,
        "R2_ACCOUNT_ID": "test-account",
        "R2_BUCKET_NAME": "test-bucket",
        "R2_ACCESS_KEY_ID": "fake-id",
        "R2_SECRET_ACCESS_KEY": "fake-key",
        "R2_VIDEO_MEDIA_BASE_URL": "https://media.example.test",
        "R2_VIDEO_MEDIA_SIGNING_SECRET": SECRET,
        "R2_VIDEO_CACHE_SOFT_LIMIT_BYTES": 1000,
        "R2_VIDEO_CACHE_HARD_LIMIT_BYTES": 2000,
        "R2_VIDEO_CACHE_MAX_OBJECT_BYTES": 1000,
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def context(*, runtime_enabled=False, ready_size=100):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    with factory() as session:
        session.add(TenantModel(id="tenant-a", name="Tenant A", slug="tenant-a"))
        session.flush()
        session.add(VideoDeliveryRuntimeSettingModel(
            setting_key=VIDEO_CDN_DELIVERY_SETTING_KEY,
            enabled=runtime_enabled,
        ))
        if ready_size is not None:
            content_hash = "d" * 64
            session.add(VideoCacheObjectModel(
                tenant_id="tenant-a",
                asset_id="asset-video",
                source_asset_id="source-video",
                content_hash=content_hash,
                r2_key=video_cache_key("tenant-a", content_hash),
                mime_type="video/mp4",
                status="ready",
                size_bytes=ready_size,
            ))
        session.commit()
    return engine, factory


def by_code(checks):
    return {check.code: check for check in checks}


def test_preflight_passes_with_runtime_off_ready_video_and_headroom():
    engine, factory = context()
    with factory() as session:
        checks = by_code(video_delivery_preflight(session, configured_settings()))
    assert all(check.ok for check in checks.values())
    assert checks["runtime_off_before_rollout"].ok
    assert checks["ready_video_available"].ok
    serialized = json.dumps({key: value.detail for key, value in checks.items()})
    assert SECRET not in serialized
    assert "media.example.test" not in serialized
    engine.dispose()


def test_preflight_fails_if_runtime_is_already_enabled():
    engine, factory = context(runtime_enabled=True)
    with factory() as session:
        checks = by_code(video_delivery_preflight(session, configured_settings()))
    assert not checks["runtime_off_before_rollout"].ok
    engine.dispose()


def test_preflight_fails_without_ready_video_or_quota_headroom():
    engine, factory = context(ready_size=1200)
    with factory() as session:
        checks = by_code(video_delivery_preflight(session, configured_settings()))
    assert not checks["quota_headroom"].ok
    engine.dispose()

    engine, factory = context(ready_size=None)
    with factory() as session:
        checks = by_code(video_delivery_preflight(session, configured_settings()))
    assert not checks["ready_video_available"].ok
    engine.dispose()


def test_preflight_rejects_public_cloudflare_origins():
    for origin in (
        "https://cache.example.r2.dev",
        "https://cam-r2.workers.dev",
        "https://test-account.r2.cloudflarestorage.com",
    ):
        engine, factory = context()
        with factory() as session:
            checks = by_code(video_delivery_preflight(
                session,
                configured_settings(R2_VIDEO_MEDIA_BASE_URL=origin),
            ))
        assert not checks["approved_media_origin"].ok
        engine.dispose()


def test_worker_probe_accepts_only_authenticated_missing_key_404():
    settings = configured_settings()

    def missing(_request, timeout):
        assert timeout == 5.0
        raise HTTPError("https://redacted.invalid", 404, "missing", {}, None)

    result = probe_worker_ticket(settings, opener=missing)
    assert result.ok
    assert result.code == "worker_signed_head_probe"
    assert SECRET not in result.detail

    def forbidden(_request, timeout):
        raise HTTPError("https://redacted.invalid", 403, "forbidden", {}, None)

    result = probe_worker_ticket(settings, opener=forbidden)
    assert not result.ok


def test_require_production_is_explicit_and_fail_closed():
    engine, factory = context()
    with factory() as session:
        checks = by_code(video_delivery_preflight(
            session,
            configured_settings(),
            require_production=True,
        ))
    assert not checks["environment"].ok
    engine.dispose()
