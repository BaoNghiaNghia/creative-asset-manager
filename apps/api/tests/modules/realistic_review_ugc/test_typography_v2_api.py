"""G1.2 feature gate, upload/batch dedup, tenant/privacy, and legacy-route safety."""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.domain.providers.contracts import StoredAsset, StoredAssetReadStream
from app.modules.authorization.principal import CurrentPrincipal, require_authenticated_principal
from app.modules.realistic_review_ugc.model import (
    RrugcReferenceAssetModel, RrugcProductModel, RrugcProductVariantModel,
    RrugcProductReferenceModel, RrugcCampaignModel, RrugcKeywordVolumeModel,
)
from app.modules.realistic_review_ugc.router import router as rrugc_router
from app.modules.realistic_review_ugc.typography_model import (
    RrugcTypographyReferenceModel, RrugcTypographyStyleDnaModel,
)


ALL_PERMISSIONS = frozenset({
    "typography_reference.read",
    "typography_reference.upload",
    "typography_reference.manage",
    "realistic_review_ugc.read",
    "realistic_review_ugc.run",
})
BASE = "/api/v1/realistic-review-ugc/typography/references"
LEGACY = "/api/v1/realistic-review-ugc/reference-assets"


def image(color: int) -> bytes:
    blob = BytesIO()
    Image.new("RGB", (48, 36), (color, 75, 130)).save(blob, format="PNG")
    return blob.getvalue()


class FakeStorage:
    def __init__(self):
        self.blobs: dict[str, bytes] = {}
        self.calls = 0

    async def store_asset(self, request):
        self.calls += 1
        chunks = [part async for part in request.body]
        data = b"".join(chunks)
        remote_id = "stored-" + str(self.calls)
        self.blobs[remote_id] = data
        return StoredAsset(storage_key=remote_id, content_hash=request.content_hash,
                           size_bytes=len(data), remote_file_id=remote_id)

    async def open_asset(self, request):
        data = self.blobs[request.remote_file_id]

        async def body():
            yield data

        async def close():
            pass

        return StoredAssetReadStream(
            body=body(), close=close,
            content_type=request.content_type or "image/png", size_bytes=len(data),
        )


def principal(user: str = "owner", tenant: str = "tenant-a", permissions=ALL_PERMISSIONS,
              platform_admin: bool = False) -> CurrentPrincipal:
    return CurrentPrincipal(
        user_id=user, active_tenant_id=tenant, membership_id="member-" + user,
        external_identity=None, effective_roles=frozenset({"operator"}),
        effective_permissions=frozenset(permissions), platform_admin=platform_admin,
        session_id="test-" + user + tenant, authorization_source="test",
    )


@dataclass
class APIEnvironment:
    client: TestClient
    context: dict
    config: SimpleNamespace
    storage: FakeStorage
    engine: object

    def identity(self, user: str, tenant: str = "tenant-a", permissions=ALL_PERMISSIONS,
                 platform_admin: bool = False):
        self.context["principal"] = principal(user, tenant, permissions, platform_admin)

    def upload(self, *colors: int, extra_files=(), **fields):
        parts = [("files", (f"idea-{i}.png", image(color), "image/png"))
                 for i, color in enumerate(colors)]
        parts.extend(extra_files)
        return self.client.post(BASE + "/uploads", data=fields, files=parts)


@pytest.fixture
def env(monkeypatch):
    import app.modules.realistic_review_ugc.typography_router as v2
    import app.modules.realistic_review_ugc.router as legacy

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    # Shared storage checks the product-reference registry before writing.
    RrugcProductModel.__table__.create(engine)
    RrugcProductVariantModel.__table__.create(engine)
    RrugcProductReferenceModel.__table__.create(engine)
    RrugcCampaignModel.__table__.create(engine)
    RrugcKeywordVolumeModel.__table__.create(engine)
    RrugcReferenceAssetModel.__table__.create(engine)
    RrugcTypographyReferenceModel.__table__.create(engine)
    RrugcTypographyStyleDnaModel.__table__.create(engine)
    AuthAuditEventModel.__table__.create(engine)
    context = {"principal": principal()}
    config = SimpleNamespace(TYPOGRAPHY_V2_ENABLED=False, TYPOGRAPHY_V2_CANARY_TENANT_IDS="")
    storage = FakeStorage()
    monkeypatch.setattr(v2, "get_settings", lambda: config)
    monkeypatch.setattr(v2, "build_managed_storage_provider", lambda _: storage)
    monkeypatch.setattr(legacy, "build_managed_storage_provider", lambda _: storage)

    app = FastAPI()
    app.include_router(rrugc_router)
    def db():
        with Session(engine, expire_on_commit=False) as session:
            yield session
    app.dependency_overrides[get_db] = db
    app.dependency_overrides[require_authenticated_principal] = lambda: context["principal"]
    with TestClient(app) as client:
        yield APIEnvironment(client, context, config, storage, engine)
    engine.dispose()


def enabled(env):
    env.config.TYPOGRAPHY_V2_ENABLED = True
    env.config.TYPOGRAPHY_V2_CANARY_TENANT_IDS = "tenant-a"


def test_default_off_does_not_expose_even_to_administrator(env):
    for method in ("get", "post"):
        env.identity("admin", platform_admin=True)
        if method == "get":
            reply = env.client.get(BASE)
        else:
            reply = env.upload(60)
        assert reply.status_code == 404
        assert reply.json()["detail"]["code"] == "typography_v2_disabled"
    assert env.storage.calls == 0
    env.config.TYPOGRAPHY_V2_ENABLED = True
    assert env.client.get(BASE).status_code == 404  # no allowlisted tenant
    env.config.TYPOGRAPHY_V2_CANARY_TENANT_IDS = "tenant-b"
    assert env.client.get(BASE).status_code == 404


def test_upload_three_items_partial_failure_dedup_and_rights_review(env):
    enabled(env)
    payload = image(100)
    response = env.upload(
        100, 100,
        extra_files=[("files", ("not-image.txt", b"no-image", "text/plain"))],
        rights="unknown", privacy="private",
    )
    assert response.status_code == 200, response.text
    doc = response.json()
    assert [item["status"] for item in doc["items"]] == ["created", "duplicate", "error"]
    assert doc["created"] == 1 and doc["duplicates"] == 1 and doc["failed"] == 1
    assert env.storage.calls == 1
    ref = doc["items"][0]["reference"]
    assert ref["influence"] == "balanced"
    assert ref["editorial_status"] == "candidate"
    assert ref["source_rights_status"] == "unknown"
    assert ref["reference_roles"] == ["layout", "font_mood", "overlap"]
    reference_id = ref["id"]
    assert env.client.get(BASE).json()["total"] == 1
    assert env.client.get(BASE + "/" + reference_id).json()["id"] == reference_id
    assert env.client.get(BASE + "/" + reference_id + "/dna").json()["items"] == []
    preview = env.client.get(BASE + "/" + reference_id + "/image")
    assert preview.status_code == 200 and preview.content == payload
    assert preview.headers["cache-control"].startswith("private")
    # Rights are verified by a manager, not silently assigned during upload.
    reject = env.client.patch(BASE + "/" + reference_id, json={"editorial_status": "approved"})
    assert reject.status_code == 422
    assert reject.json()["detail"]["code"] == "reference_rights_unverified"
    approved = env.client.patch(BASE + "/" + reference_id, json={
        "editorial_status": "approved", "source_rights_status": "licensed",
        "preference": "positive", "priority": 6,
        "tags": ["serif", "script", "serif"],
        "reference_roles": ["layout", "overlap"],
    })
    assert approved.status_code == 200, approved.text
    assert approved.json()["tags"] == ["serif", "script"]
    assert approved.json()["reference_roles"] == ["layout", "overlap"]
    assert approved.json()["editorial_status"] == "approved"
    assert approved.json()["source_rights_status"] == "licensed"
    assert env.client.delete(BASE + "/" + reference_id).status_code == 200
    assert env.client.get(BASE).json()["total"] == 0
    assert env.client.get(BASE, params={"status": "archived"}).json()["total"] == 1
    with Session(env.engine) as session:
        assets = session.query(RrugcReferenceAssetModel).all()
        assert len(assets) == 1 and assets[0].status == "ready"
        audit = session.query(AuthAuditEventModel).all()
        assert sum(event.action == "typography_reference.upload" for event in audit) == 1
        assert sum(event.action == "typography_reference.archive" for event in audit) == 1
        assert all(event.tenant_id == "tenant-a" and event.actor_id == "owner" for event in audit)
        assert all("source_url" not in event.detail_json for event in audit)


def test_legacy_rrugc_routes_cannot_reveal_private_typography_only_image(env):
    enabled(env)
    ref = env.upload(101).json()["items"][0]["reference"]
    assert env.client.get(LEGACY).json() == []
    assert env.client.get(LEGACY, params={"profile_key": "typography-idea"}).json() == []
    assert env.client.get(LEGACY + "/" + ref["asset_id"]).status_code == 404
    assert env.client.get(LEGACY + "/" + ref["asset_id"] + "/image").status_code == 404
    duplicate_upload = env.client.post(
        LEGACY + "/uploads", data={"reference_type": "other"},
        files={"file": ("duplicate.png", image(101), "image/png")},
    )
    assert duplicate_upload.status_code == 409
    assert duplicate_upload.json()["detail"]["code"] == "reference_asset_restricted"
    assert env.storage.calls == 1


def test_private_visibility_tenant_isolation_and_permissions(env):
    enabled(env)
    own = env.upload(80).json()["items"][0]["reference"]
    ref_id = own["id"]
    env.identity("visitor", permissions={"typography_reference.read"})
    assert env.client.get(BASE).json()["total"] == 0
    for path in (BASE + "/" + ref_id, BASE + "/" + ref_id + "/dna",
                 BASE + "/" + ref_id + "/image"):
        assert env.client.get(path).status_code == 404
    assert env.client.patch(BASE + "/" + ref_id, json={"notes": "intrusion"}).status_code == 403
    env.identity("viewer", permissions={"realistic_review_ugc.read", "realistic_review_ugc.run"})
    assert env.client.get(BASE).status_code == 403
    assert env.upload(85).status_code == 403
    env.identity("manager")
    assert env.client.get(BASE).json()["total"] == 1
    env.identity("outsider", tenant="tenant-b")
    assert env.client.get(BASE).status_code == 404
    env.config.TYPOGRAPHY_V2_CANARY_TENANT_IDS = "tenant-a,tenant-b"
    assert env.client.get(BASE).json()["total"] == 0
    assert env.client.get(BASE + "/" + ref_id).status_code == 404
    assert env.client.patch(BASE + "/" + ref_id, json={"notes": "cross tenant"}).status_code == 404
    assert env.client.get(BASE + "/" + ref_id + "/image").status_code == 404


def test_workspace_share_only_after_explicit_privacy_change(env):
    enabled(env)
    own = env.upload(90).json()["items"][0]["reference"]
    ref_id = own["id"]
    env.identity("colleague", permissions={"typography_reference.read"})
    assert env.client.get(BASE).json()["total"] == 0
    env.identity("manager")
    updated = env.client.patch(BASE + "/" + ref_id, json={"privacy": "workspace"})
    assert updated.status_code == 200
    env.identity("colleague", permissions={"typography_reference.read"})
    assert env.client.get(BASE).json()["total"] == 1
    assert env.client.get(BASE + "/" + ref_id + "/image").status_code == 200
    assert env.client.patch(BASE + "/" + ref_id, json={"notes": "not allowed"}).status_code == 403


def test_batch_size_file_content_and_invalid_patch_are_bounded(env):
    enabled(env)
    assert env.upload(40, extra_files=[("files", ("empty.png", b"", "image/png"))]).json()["failed"] == 1
    maxed = env.upload(*range(13))
    assert maxed.status_code == 422
    assert maxed.json()["detail"]["code"] == "typography_batch_invalid"
    # Corrupt bytes with a plausible MIME type must not create any asset.
    corrupt = env.upload(extra_files=[("files", ("broken.png", b"not png", "image/png"))])
    assert corrupt.json()["items"][0]["code"] == "reference_asset_invalid_image"
    assert env.storage.calls == 1
    ref_id = env.client.get(BASE).json()["items"][0]["id"]
    assert env.client.patch(BASE + "/" + ref_id, json={
        "reference_roles": [],
    }).status_code == 422
    assert env.client.patch(BASE + "/" + ref_id, json={
        "unknown_field": "x",
    }).status_code == 422
    assert env.client.patch(BASE + "/" + ref_id, json={
        "priority": 101,
    }).status_code == 422


def test_mime_spoof_gif_does_not_enter_typography_library(env):
    enabled(env)
    gif = BytesIO()
    Image.new("RGB", (48, 36), (20, 140, 230)).save(gif, format="GIF")
    response = env.upload(extra_files=[
        ("files", ("misleading.png", gif.getvalue(), "image/png")),
    ])
    assert response.status_code == 200
    assert response.json()["failed"] == 1
    assert response.json()["items"][0]["code"] == "reference_asset_format_unsupported"
    assert env.storage.calls == 0


def test_scope_switches_are_validated_and_cannot_cross_tenants(env):
    enabled(env)
    ref_id = env.upload(119).json()["items"][0]["reference"]["id"]
    with Session(env.engine) as session:
        campaign_a = RrugcCampaignModel(
            tenant_id="tenant-a", name="A", query="typography",
            scout_token_hash="token-hash", created_by_user_id="owner",
        )
        campaign_b = RrugcCampaignModel(
            tenant_id="tenant-b", name="B", query="hidden",
            scout_token_hash="token-hash", created_by_user_id="other",
        )
        keyword_a = RrugcKeywordVolumeModel(
            tenant_id="tenant-a", keyword="GOOD DAY", keyword_normalized="good day",
        )
        keyword_b = RrugcKeywordVolumeModel(
            tenant_id="tenant-b", keyword="HIDDEN DAY", keyword_normalized="hidden day",
        )
        session.add_all([campaign_a, campaign_b, keyword_a, keyword_b])
        session.commit()
        a_id, b_id = campaign_a.id, campaign_b.id
        ka_id, kb_id = keyword_a.id, keyword_b.id

    bad = env.client.patch(BASE + "/" + ref_id, json={
        "scope": "campaign", "campaign_id": b_id,
    })
    assert bad.status_code == 404
    assert bad.json()["detail"]["code"] == "typography_scope_not_found"

    good = env.client.patch(BASE + "/" + ref_id, json={
        "scope": "campaign", "campaign_id": a_id,
    })
    assert good.status_code == 200
    assert good.json()["scope"] == "campaign"
    assert good.json()["campaign_id"] == a_id

    reject = env.client.patch(BASE + "/" + ref_id, json={
        "scope": "keyword", "keyword_id": kb_id,
    })
    assert reject.status_code == 404
    assert env.client.get(BASE + "/" + ref_id).json()["scope"] == "campaign"

    keyword = env.client.patch(BASE + "/" + ref_id, json={
        "scope": "keyword", "keyword_id": ka_id,
    })
    assert keyword.status_code == 200
    assert keyword.json()["keyword_id"] == ka_id
    assert keyword.json()["campaign_id"] is None

    skill = env.client.patch(BASE + "/" + ref_id, json={
        "scope": "skill", "skill_name": "concept-short-v1",
    })
    assert skill.status_code == 200
    assert skill.json()["skill_name"] == "concept-short-v1"
    assert skill.json()["keyword_id"] is None

    workspace = env.client.patch(BASE + "/" + ref_id, json={"scope": "workspace"})
    assert workspace.status_code == 200
    assert workspace.json()["skill_name"] is None
    assert workspace.json()["scope"] == "workspace"
    assert env.client.patch(BASE + "/" + ref_id, json={
        "scope": "keyword",
    }).status_code == 422


def test_typography_library_filters_stats_capabilities_and_private_scopes(env):
    enabled(env)
    refs = [env.upload(value).json()["items"][0]["reference"] for value in (88, 117, 174)]
    first, second, third = (ref["id"] for ref in refs)
    env.client.patch(BASE + "/" + first, json={
        "editorial_status": "approved", "source_rights_status": "owned",
        "priority": 25, "tags": ["interlocking", "script"], "notes": "High contrast serif",
        "privacy": "workspace",
    })
    env.client.patch(BASE + "/" + second, json={
        "priority": 8, "notes": "Magazine style", "privacy": "workspace",
    })
    env.client.patch(BASE + "/" + third, json={
        "source_attribution": "Source author", "priority": 4,
    })
    page = env.client.get(BASE).json()
    assert page["stats"] == {"total": 3, "approved": 1, "needs_review": 2, "analyzing": 3}
    assert page["capabilities"] == {"read": True, "upload": True, "manage": True}
    assert env.client.get(BASE, params={"query": "Interlocking"}).json()["total"] == 1
    assert env.client.get(BASE, params={"query": "MagAziNe"}).json()["total"] == 1
    assert env.client.get(BASE, params={"query": "%"}).json()["total"] == 0
    assert env.client.get(BASE, params={"sort": "priority"}).json()["items"][0]["id"] == first
    assert env.client.get(BASE, params={"status": "approved"}).json()["total"] == 1
    assert env.client.get(BASE, params={"status": "candidate"}).json()["total"] == 2
    assert env.client.get(BASE, params={"source_type": "scout"}).json()["total"] == 0
    assert env.client.get(BASE, params={"scope": "skill"}).json()["total"] == 0
    assert env.client.get(BASE, params={"style_role": "font_mood"}).json()["total"] == 3
    assert env.client.get(BASE, params={"style_role": "script; DROP TABLE"}).status_code == 422
    assert env.client.get(BASE, params={"limit": 1, "offset": 2}).json()["items"]
    assert env.client.get(BASE, params={"limit": 0}).status_code == 422

    env.identity("colleague", permissions={"typography_reference.read"})
    visible = env.client.get(BASE).json()
    assert visible["total"] == 2
    assert visible["stats"]["total"] == 2
    assert visible["capabilities"] == {"read": True, "upload": False, "manage": False}
    assert env.client.get(BASE, params={"my_uploads_only": True}).json()["total"] == 0
    assert env.client.get(BASE + "/" + third).status_code == 404
    env.identity("owner", permissions={"typography_reference.read"})
    assert env.client.get(BASE, params={"my_uploads_only": True}).json()["total"] == 3
