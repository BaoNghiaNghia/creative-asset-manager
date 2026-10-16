"""HTTP-level authorization and tenant boundary checks for the handoff API.

Uses isolated in-memory SQLite; never touches production services.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.modules.authorization.principal import require_authenticated_principal
from app.modules.branding_dna.handoff_model import BrandHandoffCandidateModel
from app.modules.branding_dna.handoff_router import router as handoff_router
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.router import ShopCreate, add_shop
from tests.modules.branding_dna.test_handoff import packet


def principal(*perms: str, tenant: str = "tenant-1"):
    return SimpleNamespace(
        active_tenant_id=tenant, actor_id="http-reviewer",
        effective_permissions=frozenset(perms), platform_admin=False,
    )


@pytest.fixture
def app_client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[
        BrandShopModel.__table__, BrandHandoffCandidateModel.__table__,
    ])
    session = Session(engine)
    shop = add_shop(
        ShopCreate(platform="etsy", storefront_url="https://www.etsy.com/shop/ReviewCaps",
                   access_mode="authorized"),
        session=session, principal=principal("ai_provider.configure"),
    )
    app = FastAPI()
    app.include_router(handoff_router)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_authenticated_principal] = lambda: principal("ai_operations.read")
    with TestClient(app) as client:
        yield app, client, shop["id"], session
    session.close()
    engine.dispose()


def test_http_read_only_can_preview_but_cannot_stage_or_review(app_client):
    app, client, shop_id, session = app_client
    base = f"/api/v1/branding-dna/shops/{shop_id}/handoffs"
    preview = client.post(base + "/preview", json=packet())
    assert preview.status_code == 200, preview.text
    assert preview.json()["evidence_committed"] is False
    assert preview.headers["cache-control"] == "no-store"
    assert client.get(base).status_code == 200
    assert session.query(BrandHandoffCandidateModel).count() == 0
    assert client.post(base + "/stage", json=packet()).status_code == 403
    assert client.patch(base + "/00000000-0000-0000-0000-000000000000",
                        json={"status": "rejected"}).status_code == 403


def test_http_stage_is_idempotent_and_does_not_bypass_rights(app_client):
    app, client, shop_id, _ = app_client
    app.dependency_overrides[require_authenticated_principal] = lambda: principal(
        "ai_operations.read", "ai_provider.configure",
    )
    base = f"/api/v1/branding-dna/shops/{shop_id}/handoffs"
    result = client.post(base + "/stage", json=packet())
    assert result.status_code == 201, result.text
    assert result.json()["created"] == 1
    assert result.json()["evidence_committed"] is False
    again = client.post(base + "/stage", json=packet())
    assert again.status_code == 201
    assert again.json()["replayed"] == 1
    row = result.json()["items"][0]
    assert row["cam_import_eligible"] is False
    assert row["rights_state"] == "unknown"
    unauthorized = client.patch(base + "/" + row["id"],
                                json={"status": "verified", "review_note": "fake"})
    assert unauthorized.status_code == 422
    review = client.patch(base + "/" + row["id"],
                          json={"status": "needs_authorized_export", "review_note": "Need seller docs"})
    assert review.status_code == 200, review.text
    assert review.json()["item"]["status"] == "needs_authorized_export"
    assert review.json()["item"]["rights_state"] == "unknown"
    assert client.get(base).json()["count"] == 1


def test_http_cross_tenant_404_and_unknown_fields_fail_closed(app_client):
    app, client, shop_id, _ = app_client
    app.dependency_overrides[require_authenticated_principal] = lambda: principal(
        "ai_operations.read", "ai_provider.configure", tenant="other-tenant",
    )
    base = f"/api/v1/branding-dna/shops/{shop_id}/handoffs"
    assert client.get(base).status_code == 404
    assert client.post(base + "/preview", json=packet()).status_code == 404
    assert client.post(base + "/stage", json=packet()).status_code == 404
    forged = packet()
    forged["rightsVerified"] = True
    assert client.post(base + "/preview", json=forged).status_code == 422
    forged = packet()
    forged["externalImageUrls"] = ["https://example.com/sneaky.jpg"]
    assert client.post(base + "/preview", json=forged).status_code == 422


def test_http_anonymous_is_rejected(app_client):
    app, client, shop_id, _ = app_client
    def anonymous():
        raise HTTPException(status_code=401, detail="authentication_required")
    app.dependency_overrides[require_authenticated_principal] = anonymous
    base = f"/api/v1/branding-dna/shops/{shop_id}/handoffs"
    assert client.get(base).status_code == 401
    assert client.post(base + "/preview", json=packet()).status_code == 401
    assert client.post(base + "/stage", json=packet()).status_code == 401
