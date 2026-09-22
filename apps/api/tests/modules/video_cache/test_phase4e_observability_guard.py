from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.modules.auth_persistence.model import TenantModel
from app.modules.authorization.principal import CurrentPrincipal, require_platform_admin
from app.modules.public_review.video_delivery import PublicVideoDeliveryResolver
from app.modules.video_cache.admin_router import router
from app.modules.video_cache.guard import VideoDeliveryCircuitBreaker
from app.modules.video_cache.metrics import (
    delivery_observability_snapshot,
    reset_delivery_observability_for_test,
)
from app.modules.video_cache.model import (
    VIDEO_CDN_DELIVERY_SETTING_KEY,
    VideoCacheObjectModel,
    VideoDeliveryRuntimeSettingModel,
)
from app.modules.video_cache.service import video_cache_key

SECRET = "phase-4e-test-only-signing-secret-with-entropy-2026"


def configured_settings(**updates) -> Settings:
    values = {
        "R2_VIDEO_CACHE_ENABLED": True,
        "R2_ACCOUNT_ID": "test-account",
        "R2_BUCKET_NAME": "test-bucket",
        "R2_ACCESS_KEY_ID": "fake-id",
        "R2_SECRET_ACCESS_KEY": "fake-key",
        "R2_VIDEO_MEDIA_BASE_URL": "https://media.example.test",
        "R2_VIDEO_MEDIA_SIGNING_SECRET": SECRET,
        "VIDEO_CDN_DELIVERY_CANARY_TENANT_IDS": "tenant-a",
        "VIDEO_CDN_DELIVERY_GUARD_ENABLED": True,
        "VIDEO_CDN_DELIVERY_GUARD_FAILURE_THRESHOLD": 2,
        "VIDEO_CDN_DELIVERY_GUARD_PROBE_INTERVAL_SECONDS": 30,
        "VIDEO_CDN_DELIVERY_GUARD_COOLDOWN_SECONDS": 60,
        "VIDEO_CDN_DELIVERY_GUARD_TIMEOUT_SECONDS": 1.0,
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def setup():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.core.database import Base
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    with factory() as session:
        session.add(TenantModel(id="tenant-a", name="A", slug="a"))
        session.flush()
        session.add(VideoDeliveryRuntimeSettingModel(
            setting_key=VIDEO_CDN_DELIVERY_SETTING_KEY,
            enabled=True,
        ))
        session.add(VideoCacheObjectModel(
            tenant_id="tenant-a",
            asset_id="asset-video",
            source_asset_id="source-video",
            content_hash="d" * 64,
            r2_key=video_cache_key("tenant-a", "d" * 64),
            mime_type="video/mp4",
            status="ready",
            size_bytes=100,
        ))
        session.commit()
    return engine, factory


def principal():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        tenant_id="tenant-a",
        expires_at=now + timedelta(hours=1),
        session_expires_at=now + timedelta(minutes=5),
    )


def asset():
    return SimpleNamespace(
        id="asset-video", tenant_id="tenant-a", content_hash="d" * 64, mime_type="video/mp4"
    )


def source():
    return SimpleNamespace(
        id="source-video", tenant_id="tenant-a", filename="clip.mp4", mime_type="video/mp4"
    )


def test_guard_opens_after_consecutive_probe_failures_and_recovers_after_cooldown():
    now = [100.0]
    guard = VideoDeliveryCircuitBreaker(clock=lambda: now[0])
    settings = configured_settings()

    decision = guard.before_candidate(settings)
    assert decision.action == "fallback"
    assert decision.schedule_probe is True
    assert guard.complete_probe(settings, success=False) is False
    assert guard.snapshot(settings)["state"] == "degraded"

    decision = guard.before_candidate(settings)
    assert decision.action == "fallback"
    assert decision.schedule_probe is True
    assert guard.complete_probe(settings, success=False) is True
    assert guard.snapshot(settings)["state"] == "open"
    assert guard.before_candidate(settings).action == "fallback"

    now[0] += 61
    decision = guard.before_candidate(settings)
    assert decision.action == "fallback"
    assert decision.schedule_probe is True
    assert guard.complete_probe(settings, success=True) is False
    assert guard.snapshot(settings)["state"] == "closed"


def test_resolver_schedules_probe_without_running_it_inline():
    engine, factory = setup()
    guard = VideoDeliveryCircuitBreaker(clock=lambda: 100.0)
    scheduled = []
    probe_calls = []

    def probe(*_args, **_kwargs):
        probe_calls.append(True)
        return True

    resolver = PublicVideoDeliveryResolver(
        factory,
        configured_settings(),
        guard=guard,
        probe=probe,
        probe_scheduler=scheduled.append,
    )
    assert asyncio.run(resolver.resolve(principal=principal(), asset=asset(), source=source())) is None
    assert probe_calls == []
    assert len(scheduled) == 1
    assert guard.snapshot(configured_settings())["state"] == "probing"

    scheduled[0]()
    assert probe_calls == [True]
    assert guard.snapshot(configured_settings())["state"] == "closed"
    engine.dispose()


def test_resolver_probe_failure_falls_back_and_metrics_are_identity_free():
    reset_delivery_observability_for_test()
    engine, factory = setup()
    guard = VideoDeliveryCircuitBreaker(clock=lambda: 100.0)
    resolver = PublicVideoDeliveryResolver(
        factory,
        configured_settings(),
        guard=guard,
        probe=lambda *_args, **_kwargs: False,
        probe_scheduler=lambda run: run(),
    )
    assert asyncio.run(resolver.resolve(principal=principal(), asset=asset(), source=source())) is None
    snapshot = delivery_observability_snapshot()
    assert snapshot["counters"]["video_cdn_probe_failure_total"] == 1
    assert snapshot["counters"]["video_cdn_fallback_guard_total"] == 1
    serialized = str(snapshot)
    assert "tenant-a" not in serialized
    assert "asset-video" not in serialized
    assert SECRET not in serialized
    engine.dispose()


def test_resolver_healthy_probe_redirects_and_records_latency():
    reset_delivery_observability_for_test()
    engine, factory = setup()
    guard = VideoDeliveryCircuitBreaker(clock=lambda: 100.0)
    resolver = PublicVideoDeliveryResolver(
        factory,
        configured_settings(),
        guard=guard,
        probe=lambda *_args, **_kwargs: True,
        probe_scheduler=lambda run: run(),
    )
    first = asyncio.run(resolver.resolve(principal=principal(), asset=asset(), source=source()))
    assert first is None
    ticket = asyncio.run(resolver.resolve(principal=principal(), asset=asset(), source=source()))
    assert ticket is not None
    snapshot = delivery_observability_snapshot()
    assert snapshot["counters"]["video_cdn_probe_success_total"] == 1
    assert snapshot["counters"]["video_cdn_redirect_total"] == 1
    assert snapshot["decision_latency_ms"]["sample_count"] == 2
    engine.dispose()


def test_admin_observability_requires_platform_admin_and_is_safe():
    app = FastAPI()
    app.state.settings = configured_settings()
    app.include_router(router)

    def admin():
        return CurrentPrincipal(
            user_id="admin",
            active_tenant_id="tenant-a",
            membership_id="membership",
            external_identity=None,
            effective_roles=frozenset({"tenant_admin"}),
            effective_permissions=frozenset(),
            platform_admin=True,
            session_id="safe-session",
            authorization_source="test",
        )

    app.dependency_overrides[require_platform_admin] = admin
    response = TestClient(app).get("/api/v1/admin/video-delivery/observability")
    assert response.status_code == 200
    body = response.json()
    assert body["metrics"]["scope"] == "process_local"
    assert body["guard"]["scope"] == "process_local"
    serialized = response.text
    assert "tenant-a" not in serialized
    assert SECRET not in serialized
    assert "media.example.test" not in serialized


def test_guard_settings_reject_unsafe_thresholds():
    import pytest

    for updates in (
        {"VIDEO_CDN_DELIVERY_GUARD_FAILURE_THRESHOLD": 0},
        {"VIDEO_CDN_DELIVERY_GUARD_PROBE_INTERVAL_SECONDS": 4},
        {"VIDEO_CDN_DELIVERY_GUARD_COOLDOWN_SECONDS": 9},
        {"VIDEO_CDN_DELIVERY_GUARD_TIMEOUT_SECONDS": 0.0},
    ):
        with pytest.raises(ValueError):
            configured_settings(**updates)


def test_probe_exception_falls_back_without_leaving_guard_stuck():
    engine, factory = setup()
    guard = VideoDeliveryCircuitBreaker(clock=lambda: 100.0)

    def explode(*_args, **_kwargs):
        raise RuntimeError("synthetic probe failure")

    resolver = PublicVideoDeliveryResolver(
        factory,
        configured_settings(),
        guard=guard,
        probe=explode,
        probe_scheduler=lambda run: run(),
    )
    assert asyncio.run(resolver.resolve(principal=principal(), asset=asset(), source=source())) is None
    assert guard.snapshot(configured_settings())["state"] == "degraded"
    engine.dispose()
