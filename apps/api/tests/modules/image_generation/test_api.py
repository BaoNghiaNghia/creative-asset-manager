from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base, get_db
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.authorization.principal import CurrentPrincipal, require_authenticated_principal
from app.modules.image_generation.router import router


def principal(tenant="tenant-a", permissions=frozenset({"assets.read", "assets.generate"})):
    return CurrentPrincipal(
        user_id="user-a",
        active_tenant_id=tenant,
        membership_id="membership-a",
        external_identity=None,
        effective_roles=frozenset({"operator"}),
        effective_permissions=permissions,
        platform_admin=False,
        session_id="session",
        authorization_source="test",
    )


@pytest.fixture
def api(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        asset = AssetModel(tenant_id="tenant-a", content_hash="a" * 64, mime_type="image/png")
        external = ExternalSourceModel(
            tenant_id="tenant-a",
            source_key="source",
            source_type="google_drive",
            source_metadata={"oauth_connection_id": "connection"},
        )
        session.add_all([asset, external])
        session.flush()
        source = SourceAssetModel(
            tenant_id="tenant-a",
            external_source_id=external.id,
            external_asset_id="file",
            filename="photo.png",
            mime_type="image/png",
            size_bytes=100,
        )
        session.add(source)
        session.flush()
        session.add(AssetSourceLinkModel(tenant_id="tenant-a", asset_id=asset.id, source_asset_id=source.id))
        session.commit()
        asset_id = asset.id

    configured = Settings(
        IMAGE_GENERATION_ENABLED=True,
        FIREFLY_IMAGE_GENERATION_ENABLED=True,
        FIREFLY_SERVICES_CLIENT_ID="id",
        FIREFLY_SERVICES_CLIENT_SECRET="secret",
        GEMINI_IMAGE_GENERATION_ENABLED=False,
        MANAGED_ASSET_STORAGE_ENABLED=True,
        IMAGE_GENERATION_STAGING_ROOT=str(tmp_path),
    )
    monkeypatch.setattr("app.modules.image_generation.router.get_settings", lambda: configured)
    app = FastAPI()
    app.include_router(router)

    def database():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[require_authenticated_principal] = lambda: principal()
    try:
        with TestClient(app) as client:
            yield client, app, factory, asset_id
    finally:
        engine.dispose()


def payload(asset_id, request_id="00000000-0000-4000-8000-000000000010"):
    return {
        "source_asset_id": asset_id,
        "provider": "adobe_firefly",
        "target_size": 1024,
        "prompt": None,
        "client_request_id": request_id,
    }


def test_create_get_idempotency_and_cancel(api):
    client, _, factory, asset_id = api
    first = client.post("/api/v1/image-generations/square", json=payload(asset_id))
    second = client.post("/api/v1/image-generations/square", json=payload(asset_id))
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    generation_id = first.json()["id"]
    fetched = client.get(f"/api/v1/image-generations/{generation_id}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "queued"
    cancelled = client.post(f"/api/v1/image-generations/{generation_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_invalid_target_provider_and_unavailable_provider(api):
    client, _, _, asset_id = api
    invalid_size = payload(asset_id, "00000000-0000-4000-8000-000000000011")
    invalid_size["target_size"] = 512
    assert client.post("/api/v1/image-generations/square", json=invalid_size).status_code == 422
    invalid_provider = payload(asset_id, "00000000-0000-4000-8000-000000000012")
    invalid_provider["provider"] = "third-provider"
    assert client.post("/api/v1/image-generations/square", json=invalid_provider).status_code == 422
    gemini = payload(asset_id, "00000000-0000-4000-8000-000000000013")
    gemini["provider"] = "gemini"
    response = client.post("/api/v1/image-generations/square", json=gemini)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "gemini_not_configured"


def test_permission_denied_and_cross_tenant_non_disclosure(api):
    client, app, _, asset_id = api
    app.dependency_overrides[require_authenticated_principal] = lambda: principal(
        permissions=frozenset({"assets.read"})
    )
    assert client.post("/api/v1/image-generations/square", json=payload(asset_id)).status_code == 403
    app.dependency_overrides[require_authenticated_principal] = lambda: principal("tenant-b")
    assert client.get("/api/v1/image-generations/00000000-0000-4000-8000-000000000099").status_code == 404


def test_capabilities_safe_shape(api):
    client, _, _, _ = api
    response = client.get("/api/v1/image-generations/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["target_sizes"] == [1024, 2048]
    assert {item["id"] for item in body["providers"]} == {"adobe_firefly", "cloudflare_sd", "gemini"}
    assert all("secret" not in item for item in body["providers"])
