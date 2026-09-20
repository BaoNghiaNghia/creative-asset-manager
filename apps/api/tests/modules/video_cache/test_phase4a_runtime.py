import json
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.authorization.principal import CurrentPrincipal, require_platform_admin
from app.modules.video_cache.admin_router import router
from app.modules.video_cache.model import (
    VIDEO_CDN_DELIVERY_SETTING_KEY,
    VideoDeliveryRuntimeSettingModel,
)
from app.modules.video_cache.runtime import (
    VideoDeliveryPrerequisiteError,
    VideoDeliveryRuntimeService,
)


SIGNING_SECRET = "phase-4a-test-only-signing-secret-with-entropy-2026"


def configured_settings(**updates) -> Settings:
    values = {
        "R2_VIDEO_CACHE_ENABLED": True,
        "R2_ACCOUNT_ID": "test-account",
        "R2_BUCKET_NAME": "test-bucket",
        "R2_ACCESS_KEY_ID": "fake-id",
        "R2_SECRET_ACCESS_KEY": "fake-key",
        "R2_VIDEO_MEDIA_BASE_URL": "https://media.example.test",
        "R2_VIDEO_MEDIA_SIGNING_SECRET": SIGNING_SECRET,
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def make_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VideoDeliveryRuntimeSettingModel.__table__.create(engine)
    AuthAuditEventModel.__table__.create(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    with factory() as session:
        session.add(
            VideoDeliveryRuntimeSettingModel(
                setting_key=VIDEO_CDN_DELIVERY_SETTING_KEY,
                enabled=False,
            )
        )
        session.commit()
    return engine, factory


def principal(*, platform_admin: bool) -> CurrentPrincipal:
    return CurrentPrincipal(
        user_id="user-admin" if platform_admin else "user-viewer",
        active_tenant_id="tenant-a",
        membership_id="membership-a",
        external_identity=None,
        effective_roles=frozenset({"tenant_admin"} if platform_admin else {"viewer"}),
        effective_permissions=frozenset(),
        platform_admin=platform_admin,
        session_id="safe-session-hash",
        authorization_source="test",
    )


def test_runtime_default_is_off_even_when_prerequisites_are_ready():
    engine, factory = make_factory()
    with factory() as session:
        status = VideoDeliveryRuntimeService(
            session, configured_settings()
        ).get_status()
    assert status["runtime_enabled"] is False
    assert status["effective_enabled"] is False
    assert status["can_enable"] is True
    assert status["blockers"] == ["runtime_toggle_disabled"]
    engine.dispose()


def test_enable_persists_audits_and_never_serializes_delivery_secret():
    engine, factory = make_factory()
    with factory() as session:
        status = VideoDeliveryRuntimeService(
            session, configured_settings()
        ).set_enabled(
            True,
            actor_id="platform-admin",
            reason="approved local Phase 4A validation",
        )
        session.commit()
    assert status["runtime_enabled"] is True
    assert status["effective_enabled"] is True
    with factory() as session:
        row = session.get(
            VideoDeliveryRuntimeSettingModel, VIDEO_CDN_DELIVERY_SETTING_KEY
        )
        audit = session.scalar(
            select(AuthAuditEventModel).where(
                AuthAuditEventModel.action == "video_cdn_delivery_runtime_updated"
            )
        )
        assert row is not None and row.enabled is True
        assert audit is not None
        serialized = json.dumps(audit.detail_json)
        assert SIGNING_SECRET not in serialized
        assert "media.example.test" not in serialized
    engine.dispose()


def test_enable_fails_closed_when_any_prerequisite_is_missing():
    engine, factory = make_factory()
    for settings in (
        Settings(_env_file=None),
        configured_settings(R2_VIDEO_MEDIA_BASE_URL=""),
        configured_settings(R2_VIDEO_MEDIA_SIGNING_SECRET=""),
    ):
        with factory() as session:
            service = VideoDeliveryRuntimeService(session, settings)
            with pytest.raises(VideoDeliveryPrerequisiteError):
                service.set_enabled(
                    True,
                    actor_id="platform-admin",
                    reason="attempt enable without prerequisites",
                )
            session.rollback()
            assert service.get_status()["effective_enabled"] is False
    with factory() as session:
        assert session.get(
            VideoDeliveryRuntimeSettingModel, VIDEO_CDN_DELIVERY_SETTING_KEY
        ).enabled is False
    engine.dispose()


def test_disable_is_allowed_when_server_prerequisites_are_no_longer_ready():
    engine, factory = make_factory()
    with factory() as session:
        service = VideoDeliveryRuntimeService(session, configured_settings())
        service.set_enabled(
            True,
            actor_id="platform-admin",
            reason="enable for local test",
        )
        session.commit()
    with factory() as session:
        status = VideoDeliveryRuntimeService(
            session, Settings(_env_file=None)
        ).set_enabled(
            False,
            actor_id="platform-admin",
            reason="safe rollback",
        )
        session.commit()
    assert status["runtime_enabled"] is False
    assert status["effective_enabled"] is False
    engine.dispose()


def test_admin_router_requires_platform_admin_and_returns_safe_status():
    engine, factory = make_factory()
    app = FastAPI()
    app.state.settings = configured_settings()
    app.include_router(router)
    app.dependency_overrides[require_platform_admin] = lambda: principal(
        platform_admin=True
    )
    client = TestClient(app)
    with patch("app.modules.video_cache.admin_router.SessionLocal", factory):
        response = client.get("/api/v1/admin/video-delivery/runtime")
        assert response.status_code == 200
        assert response.json()["runtime_enabled"] is False
        updated = client.put(
            "/api/v1/admin/video-delivery/runtime",
            json={"enabled": True, "reason": "approved local toggle"},
        )
        assert updated.status_code == 200
        assert updated.json()["effective_enabled"] is True
    serialized = response.text + updated.text
    assert SIGNING_SECRET not in serialized
    assert "media.example.test" not in serialized

    route = next(
        item
        for item in app.routes
        if getattr(item, "path", "") == "/api/v1/admin/video-delivery/runtime"
        and "GET" in item.methods
    )
    assert route.dependant.dependencies[0].call is require_platform_admin
    with pytest.raises(HTTPException) as denied:
        require_platform_admin(principal(platform_admin=False))
    assert denied.value.status_code == 403
    engine.dispose()


def test_missing_runtime_row_is_503_not_an_implicit_default():
    engine, factory = make_factory()
    with factory() as session:
        session.query(VideoDeliveryRuntimeSettingModel).delete()
        session.commit()

    app = FastAPI()
    app.state.settings = configured_settings()
    app.include_router(router)
    app.dependency_overrides[require_platform_admin] = lambda: principal(
        platform_admin=True
    )
    client = TestClient(app)
    with patch("app.modules.video_cache.admin_router.SessionLocal", factory):
        response = client.get("/api/v1/admin/video-delivery/runtime")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "video_delivery_runtime_unavailable"
    engine.dispose()


def test_0084_sqlite_migration_round_trip():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as directory:
        url = f"sqlite:///{Path(directory) / 'phase4a.sqlite'}"
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "0083_r2_video_cache_fill")
        command.upgrade(config, "0084_video_cdn_delivery_runtime")

        engine = create_engine(url)
        assert "video_delivery_runtime_settings" in inspect(engine).get_table_names()
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT setting_key, enabled "
                    "FROM video_delivery_runtime_settings"
                )
            ).mappings().one()
        assert row["setting_key"] == VIDEO_CDN_DELIVERY_SETTING_KEY
        assert bool(row["enabled"]) is False
        engine.dispose()

        command.downgrade(config, "0083_r2_video_cache_fill")
        engine = create_engine(url)
        assert "video_delivery_runtime_settings" not in inspect(engine).get_table_names()
        engine.dispose()

        command.upgrade(config, "0084_video_cdn_delivery_runtime")
        engine = create_engine(url)
        assert "video_delivery_runtime_settings" in inspect(engine).get_table_names()
        engine.dispose()
