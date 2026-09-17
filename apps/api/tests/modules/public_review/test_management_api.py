from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel, SourceAssetModel
from app.modules.auth_persistence.model import AuthAuditEventModel, OAuthConnectionModel, TenantModel
from app.modules.authorization.principal import CurrentPrincipal
from app.modules.public_review.repository import PublicReviewRepository
from app.modules.public_review.router import MANAGE_PUBLIC_REVIEW, router
from app.modules.public_review.service import PublicReviewService


@pytest.fixture()
def context():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    with factory() as db:
        db.add_all([TenantModel(id="tenant-a", name="A", slug="a"), TenantModel(id="tenant-b", name="B", slug="b")])
        db.flush()
        db.add_all([OAuthConnectionModel(id="conn-a", tenant_id="tenant-a", provider="google", provider_account_id="a", key_version="v1"), OAuthConnectionModel(id="conn-b", tenant_id="tenant-b", provider="google", provider_account_id="b", key_version="v1")])
        db.flush()
        db.add_all([ExternalSourceModel(id="source-a", tenant_id="tenant-a", source_key="a", source_type="google_drive", oauth_connection_id="conn-a"), ExternalSourceModel(id="source-b", tenant_id="tenant-b", source_key="b", source_type="google_drive", oauth_connection_id="conn-b")])
        db.flush()
        db.add_all([SourceAssetModel(id="folder-a", tenant_id="tenant-a", external_source_id="source-a", external_asset_id="folder-a"), SourceAssetModel(id="folder-b", tenant_id="tenant-b", external_source_id="source-b", external_asset_id="folder-b")])
        db.commit()
    principal = CurrentPrincipal(user_id="user-a", active_tenant_id="tenant-a", membership_id="membership-a", external_identity=None, effective_roles=frozenset(), effective_permissions=frozenset({"public_review.manage"}), platform_admin=False, session_id="safe", authorization_source="test")
    app = FastAPI()
    app.include_router(router)
    for route in app.routes:
        if getattr(route, "path", "").startswith("/api/v1/public-review/"):
            app.dependency_overrides[route.dependant.dependencies[0].call] = lambda: principal
    yield TestClient(app), factory, principal
    engine.dispose()


def request(context, method, path, **kwargs):
    client, factory, _ = context
    with patch("app.modules.public_review.router.SessionLocal", factory):
        return client.request(method, path, **kwargs)


def payload(**overrides):
    value = {"name": "Review", "scopes": [{"external_source_id": "source-a", "folder_external_id": "folder-a"}], "allow_comments": True, "allow_download": False}
    value.update(overrides)
    return value


def test_management_create_read_rotate_revoke_and_secret_safety(context):
    created = request(context, "POST", "/api/v1/public-review/shares", json=payload())
    assert created.status_code == 201
    item = created.json(); share_id = item["id"]; first_url = item["share_url"]
    assert "#key=" in first_url and "secret_digest" not in created.text
    listed = request(context, "GET", "/api/v1/public-review/shares")
    fetched = request(context, "GET", f"/api/v1/public-review/shares/{share_id}")
    assert "share_url" not in listed.text + fetched.text
    assert "secret_digest" not in listed.text + fetched.text
    rotated = request(context, "POST", f"/api/v1/public-review/shares/{share_id}/rotate-secret")
    assert rotated.status_code == 200 and rotated.json()["share_url"] != first_url
    revoked = request(context, "DELETE", f"/api/v1/public-review/shares/{share_id}")
    repeated = request(context, "DELETE", f"/api/v1/public-review/shares/{share_id}")
    assert revoked.json()["status"] == repeated.json()["status"] == "revoked"
    _, factory, _ = context
    with factory() as db:
        share = PublicReviewRepository(db).get_share("tenant-a", share_id)
        assert share.secret_digest not in {first_url, rotated.json()["share_url"]}
        actions = list(db.scalars(select(AuthAuditEventModel.action).where(AuthAuditEventModel.tenant_id == "tenant-a")))
        audit = str(list(db.scalars(select(AuthAuditEventModel.detail_json).where(AuthAuditEventModel.tenant_id == "tenant-a"))))
    assert {"public_review_share_created", "public_review_share_secret_rotated", "public_review_share_revoked"} <= set(actions)
    assert "#key=" not in audit and share.secret_digest not in audit


def test_management_rejects_foreign_scope_expiry_and_foreign_share(context):
    foreign = request(context, "POST", "/api/v1/public-review/shares", json=payload(scopes=[{"external_source_id": "source-b", "folder_external_id": "folder-b"}]))
    assert foreign.status_code == 404
    invalid_expiry = request(context, "POST", "/api/v1/public-review/shares", json=payload(expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()))
    assert invalid_expiry.status_code == 422
    with context[1]() as db:
        share, _ = PublicReviewService(PublicReviewRepository(db)).create_managed_share(tenant_id="tenant-a", actor_id="user-a", name="Private", scopes=[{"external_source_id": "source-a", "folder_external_id": "folder-a"}])
        db.commit()
    other = CurrentPrincipal(user_id="user-b", active_tenant_id="tenant-b", membership_id="m-b", external_identity=None, effective_roles=frozenset(), effective_permissions=frozenset({"public_review.manage"}), platform_admin=False, session_id="safe-b", authorization_source="test")
    client, factory, _ = context
    for route in client.app.routes:
        if getattr(route, "path", "").startswith("/api/v1/public-review/"):
            client.app.dependency_overrides[route.dependant.dependencies[0].call] = lambda: other
    with patch("app.modules.public_review.router.SessionLocal", factory):
        response = client.get(f"/api/v1/public-review/shares/{share.id}")
    assert response.status_code == 404


def test_permission_is_required_and_public_principal_is_not_accepted(context):
    denied = CurrentPrincipal(user_id="user-a", active_tenant_id="tenant-a", membership_id="m", external_identity=None, effective_roles=frozenset(), effective_permissions=frozenset(), platform_admin=False, session_id="safe", authorization_source="test")
    with pytest.raises(HTTPException) as captured:
        MANAGE_PUBLIC_REVIEW(denied)
    assert captured.value.status_code == 403


def test_rotation_and_revoke_invalidate_old_secret_and_active_sessions(context):
    _, factory, _ = context
    with factory() as db:
        service = PublicReviewService(PublicReviewRepository(db))
        share, old_secret = service.create_managed_share(tenant_id="tenant-a", actor_id="user-a", name="Review", scopes=[{"external_source_id": "source-a", "folder_external_id": "folder-a"}])
        session = service.create_session(tenant_id="tenant-a", share_id=share.id, raw_session_token="clearly-fake-session", expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
        _, new_secret = service.rotate_managed_share_secret(tenant_id="tenant-a", actor_id="user-a", share_id=share.id)
        assert new_secret != old_secret
        with pytest.raises(LookupError):
            service.verify_share_secret(share.public_id, old_secret)
        with pytest.raises(LookupError):
            service.resolve_session(tenant_id="tenant-a", raw_session_token="clearly-fake-session")
        service.revoke_managed_share(tenant_id="tenant-a", actor_id="user-a", share_id=share.id)
        assert session.revoked_at is not None
