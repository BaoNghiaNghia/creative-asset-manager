from __future__ import annotations

import json

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
from app.operations.video_delivery_activation import activation_readiness
from app.operations.video_delivery_preflight import PreflightCheck

SECRET = "phase-5a-test-signing-secret-with-strong-entropy-2026"


def settings(**updates) -> Settings:
    values = {
        "APP_ENV": "production",
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


def context(*, runtime_enabled=False):
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
        content_hash = "d" * 64
        session.add(VideoCacheObjectModel(
            tenant_id="tenant-a",
            asset_id="asset-video",
            source_asset_id="source-video",
            content_hash=content_hash,
            r2_key=video_cache_key("tenant-a", content_hash),
            mime_type="video/mp4",
            status="ready",
            size_bytes=100,
        ))
        session.commit()
    return engine, factory


def ok_worker(_settings):
    return PreflightCheck(
        code="worker_signed_head_probe",
        ok=True,
        detail="Worker signed probe passed",
    )


def ok_ready(_session, _settings):
    return PreflightCheck(
        code="worker_ready_head_probe",
        ok=True,
        detail="Worker READY probe passed",
    )


def test_activation_gate_is_ready_only_after_all_read_only_checks_pass():
    engine, factory = context()
    with factory() as session:
        result = activation_readiness(
            session,
            settings(),
            worker_probe=ok_worker,
            ready_probe=ok_ready,
        )
    assert result["ready_to_enable"] is True
    assert result["runtime"]["runtime_enabled"] is False
    assert result["runtime"]["can_enable"] is True
    assert result["next_action"].startswith("enable the persisted runtime toggle")
    serialized = json.dumps(result)
    assert SECRET not in serialized
    assert "tenant-a" not in serialized
    assert "asset-video" not in serialized
    assert "media.example.test" not in serialized
    engine.dispose()


def test_activation_gate_fails_if_runtime_is_already_enabled():
    engine, factory = context(runtime_enabled=True)
    with factory() as session:
        result = activation_readiness(
            session,
            settings(),
            worker_probe=ok_worker,
            ready_probe=ok_ready,
        )
    assert result["ready_to_enable"] is False
    by_code = {item["code"]: item for item in result["checks"]}
    assert by_code["runtime_off_before_rollout"]["ok"] is False
    engine.dispose()


def test_activation_gate_fails_if_guard_is_disabled():
    engine, factory = context()
    with factory() as session:
        result = activation_readiness(
            session,
            settings(VIDEO_CDN_DELIVERY_GUARD_ENABLED=False),
            worker_probe=ok_worker,
            ready_probe=ok_ready,
        )
    assert result["ready_to_enable"] is False
    assert result["runtime"]["can_enable"] is False
    assert result["runtime"]["prerequisites"]["delivery_guard_enabled"] is False
    engine.dispose()


def test_activation_gate_fails_on_either_worker_probe():
    engine, factory = context()
    failed = PreflightCheck(
        code="worker_signed_head_probe",
        ok=False,
        detail="Worker signed probe failed",
    )
    with factory() as session:
        result = activation_readiness(
            session,
            settings(),
            worker_probe=lambda _settings: failed,
            ready_probe=ok_ready,
        )
    assert result["ready_to_enable"] is False

    failed_ready = PreflightCheck(
        code="worker_ready_head_probe",
        ok=False,
        detail="Worker READY probe failed",
    )
    with factory() as session:
        result = activation_readiness(
            session,
            settings(),
            worker_probe=ok_worker,
            ready_probe=lambda _session, _settings: failed_ready,
        )
    assert result["ready_to_enable"] is False
    engine.dispose()


def test_global_rollout_requires_explicit_activation_override():
    engine, factory = context()
    global_settings = settings(
        VIDEO_CDN_DELIVERY_CANARY_TENANT_IDS="",
        VIDEO_CDN_DELIVERY_GLOBAL_ROLLOUT_ENABLED=True,
    )
    with factory() as session:
        strict = activation_readiness(
            session,
            global_settings,
            worker_probe=ok_worker,
            ready_probe=ok_ready,
        )
        explicit = activation_readiness(
            session,
            global_settings,
            allow_global_rollout=True,
            worker_probe=ok_worker,
            ready_probe=ok_ready,
        )
    assert strict["ready_to_enable"] is False
    assert explicit["ready_to_enable"] is True
    engine.dispose()
