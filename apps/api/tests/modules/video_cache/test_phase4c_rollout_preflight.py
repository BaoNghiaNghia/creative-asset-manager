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
    probe_ready_video_ticket,
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
        "VIDEO_CDN_DELIVERY_CANARY_TENANT_IDS": "tenant-a",
        "VIDEO_CDN_DELIVERY_GUARD_ENABLED": True,
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


def test_preflight_rejects_public_r2_origins_even_with_workers_dev_opt_in():
    for origin in (
        "https://cache.example.r2.dev",
        "https://test-account.r2.cloudflarestorage.com",
    ):
        engine, factory = context()
        with factory() as session:
            checks = by_code(video_delivery_preflight(
                session,
                configured_settings(
                    R2_VIDEO_MEDIA_BASE_URL=origin,
                    R2_VIDEO_MEDIA_ALLOW_WORKERS_DEV=True,
                ),
            ))
        assert not checks["approved_media_origin"].ok
        assert not checks["delivery_configured"].ok
        engine.dispose()


def test_preflight_workers_dev_requires_explicit_operator_opt_in():
    origin = "https://cam-r2-original-video.baonghia-kht.workers.dev"

    engine, factory = context()
    with factory() as session:
        denied = by_code(video_delivery_preflight(
            session,
            configured_settings(R2_VIDEO_MEDIA_BASE_URL=origin),
        ))
        allowed = by_code(video_delivery_preflight(
            session,
            configured_settings(
                R2_VIDEO_MEDIA_BASE_URL=origin,
                R2_VIDEO_MEDIA_ALLOW_WORKERS_DEV=True,
            ),
        ))
    assert not denied["approved_media_origin"].ok
    assert not denied["delivery_configured"].ok
    assert not denied["workers_dev_operator_opt_in"].ok
    assert allowed["approved_media_origin"].ok
    assert allowed["delivery_configured"].ok
    assert allowed["workers_dev_operator_opt_in"].ok
    assert "explicit operator opt-in" in allowed["workers_dev_operator_opt_in"].detail
    engine.dispose()


def test_preflight_custom_domain_behavior_is_unchanged():
    engine, factory = context()
    with factory() as session:
        checks = by_code(video_delivery_preflight(
            session,
            configured_settings(R2_VIDEO_MEDIA_BASE_URL="https://media.example.test"),
        ))
    assert checks["approved_media_origin"].ok
    assert checks["delivery_configured"].ok
    assert "workers_dev_operator_opt_in" not in checks
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

    def forbidden(request, timeout):
        assert request.get_header("User-agent") == "creative-asset-manager-preflight"
        raise HTTPError("https://redacted.invalid", 403, "forbidden", {}, None)

    result = probe_worker_ticket(settings, opener=forbidden)
    assert not result.ok
    assert "HTTP 403" in result.detail


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


def test_preflight_canary_scope_is_fail_closed_and_global_requires_explicit_override():
    engine, factory = context()
    global_settings = configured_settings(
        VIDEO_CDN_DELIVERY_CANARY_TENANT_IDS="",
        VIDEO_CDN_DELIVERY_GLOBAL_ROLLOUT_ENABLED=True,
    )
    with factory() as session:
        strict = by_code(video_delivery_preflight(session, global_settings))
        relaxed = by_code(video_delivery_preflight(
            session,
            global_settings,
            require_canary_scope=False,
        ))
    assert not strict["canary_rollout_scope"].ok
    assert "canary_rollout_scope" not in relaxed
    engine.dispose()


def test_ready_object_probe_uses_head_metadata_only_and_never_exposes_identity():
    engine, factory = context()

    class Response:
        status = 200
        headers = {
            "Content-Length": "100",
            "Content-Type": "video/mp4",
            "Accept-Ranges": "bytes",
        }
        def close(self):
            pass

    def ok(request, timeout):
        assert request.get_method() == "HEAD"
        assert timeout == 5.0
        return Response()

    with factory() as session:
        result = probe_ready_video_ticket(
            session,
            configured_settings(),
            opener=ok,
        )
    assert result.ok
    serialized = json.dumps(result.__dict__)
    assert "tenant-a" not in serialized
    assert "asset-video" not in serialized
    assert SECRET not in serialized
    engine.dispose()


def test_video_delivery_canary_settings_reject_ambiguous_or_invalid_scope():
    import pytest

    with pytest.raises(ValueError):
        configured_settings(
            VIDEO_CDN_DELIVERY_CANARY_TENANT_IDS="tenant-a,tenant-a",
        )
    with pytest.raises(ValueError):
        configured_settings(
            VIDEO_CDN_DELIVERY_CANARY_TENANT_IDS="../tenant",
        )
    with pytest.raises(ValueError):
        configured_settings(
            VIDEO_CDN_DELIVERY_GLOBAL_ROLLOUT_ENABLED=True,
        )
