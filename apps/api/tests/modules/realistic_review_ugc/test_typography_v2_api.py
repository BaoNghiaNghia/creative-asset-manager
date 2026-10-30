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
    RrugcTypographyAnalysisJobModel,
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
    RrugcTypographyAnalysisJobModel.__table__.create(engine)
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



def test_design_scout_requires_agent_and_explicit_flag_and_preserves_review_gate(env, monkeypatch):
    """Scout token may create a candidate, never grant human source rights."""
    import app.modules.realistic_review_ugc.scout_automation as scout_auth

    def authenticate(self, *, agent_id, raw_token, lock=False):
        if agent_id != "agent-a" or raw_token != "paired-secret":
            from app.modules.realistic_review_ugc.service import RrugcError
            raise RrugcError("auth_failure", "Invalid credentials", status_code=401)
        return SimpleNamespace(id=agent_id, tenant_id="tenant-a", active=True)

    monkeypatch.setattr(scout_auth.RrugcAutoScoutService, "authenticate_agent", authenticate)
    enabled(env)
    with Session(env.engine, expire_on_commit=False) as db:
        keyword = RrugcKeywordVolumeModel(
            tenant_id="tenant-a", keyword="ADVENTURE AWAITS",
            keyword_normalized="adventure awaits", picked=True,
        )
        foreign = RrugcKeywordVolumeModel(
            tenant_id="tenant-b", keyword="OTHER TENANT", keyword_normalized="other tenant",
            picked=True,
        )
        db.add_all((keyword, foreign))
        db.commit()
        keyword_id, foreign_id = keyword.id, foreign.id

    base = "/api/v1/realistic-review-ugc/typography/scout-agents/agent-a"
    endpoint = base + "/references"
    form = {
        "source_url": "https://www.pinterest.com/pin/123456789012345/",
        "source_query": "ADVENTURE AWAITS embroidered cap",
        "keyword_id": keyword_id,
    }
    upload = {"file": ("pin.jpg", image(180), "image/png")}
    denied = env.client.post(endpoint, files=upload, data=form)
    assert denied.status_code == 401
    off = env.client.post(endpoint, headers={"Authorization": "Bearer paired-secret"},
                          files=upload, data=form)
    assert off.status_code == 404
    env.config.TYPOGRAPHY_V2_DESIGN_SCOUT_ENABLED = True
    header = {"Authorization": "Bearer paired-secret"}
    wrong = env.client.post(endpoint, headers={"Authorization": "Bearer wrong"},
                            files=upload, data=form)
    assert wrong.status_code == 401
    assert env.client.get(base + "/keywords", headers=header).json()["keywords"][0]["keyword"] == "ADVENTURE AWAITS"
    wrong_domain = env.client.post(endpoint, headers=header,
                                   files=upload, data={**form, "source_url": "https://evil.example/pin/123456789012345/"})
    assert wrong_domain.status_code == 422
    foreign_keyword = env.client.post(endpoint, headers=header,
                                      files=upload, data={**form, "keyword_id": foreign_id})
    assert foreign_keyword.status_code == 404
    created = env.client.post(endpoint, headers=header, files=upload, data=form)
    assert created.status_code == 200, created.text
    assert created.json()["status"] == "created"
    assert created.json()["editorial_status"] == "candidate"
    assert created.json()["source_rights_status"] == "unknown"
    repeated = env.client.post(endpoint, headers=header, files=upload, data=form)
    assert repeated.json()["status"] == "duplicate"
    assert env.storage.calls == 1
    with Session(env.engine) as db:
        rows = db.query(RrugcTypographyReferenceModel).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.source_type == "scout" and row.source_platform == "pinterest"
        assert row.source_url == form["source_url"]
        assert row.keyword_id == keyword_id
        assert row.privacy == "workspace"
        assert row.editorial_status == "candidate" and row.analysis_status == "pending"
        assert row.source_rights_status == "unknown"
        assert db.query(RrugcTypographyStyleDnaModel).count() == 0
        assert any(event.action == "typography_reference.scout_candidate"
                   for event in db.query(AuthAuditEventModel).all())
    # Unverified rights remain unapprovable, even after an agent upload.
    assert env.client.patch(BASE + "/" + created.json()["reference_id"],
                            json={"editorial_status": "approved"}).status_code == 422

def test_status_probe_preserves_canary_and_permission_boundaries(env):
    status = "/api/v1/realistic-review-ugc/typography/status"
    assert env.client.get(status).json() == {"available": False}
    assert env.client.get(BASE).status_code == 404
    enabled(env)
    assert env.client.get(status).json() == {"available": True}
    env.identity("viewer", permissions={"realistic_review_ugc.read"})
    assert env.client.get(status).json() == {"available": False}
    assert env.client.get(BASE).status_code == 403
    env.identity("outsider", tenant="tenant-b")
    assert env.client.get(status).json() == {"available": False}
    assert env.client.get(BASE).status_code == 404


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


def test_dna_cache_is_tenant_private_hash_and_version_scoped(env):
    from app.modules.realistic_review_ugc.typography_repository import TypographyReferenceRepository

    enabled(env)
    private = env.upload(91).json()["items"][0]["reference"]
    shared = env.upload(92).json()["items"][0]["reference"]
    env.client.patch(BASE + "/" + shared["id"], json={"privacy": "workspace"})
    with Session(env.engine, expire_on_commit=False) as session:
        repo = TypographyReferenceRepository(session)
        dna = RrugcTypographyStyleDnaModel(
            tenant_id="tenant-a", reference_id=private["id"],
            analyzer_version="pilot-1", image_hash=private["content_hash"],
            typography_region_json={"x": 0.1, "y": 0.2, "w": 0.7, "h": 0.5},
            style_family="serif", font_mood_json={"primary": "serif"},
            layout_graph_json={"nodes": [], "edges": []}, overlap_rules_json=[],
            confidence=0.8,
        )
        session.add(dna)
        session.flush()
        lookup = dict(tenant_id="tenant-a", ref_id=private["id"],
                      analyzer_version="pilot-1", image_hash=private["content_hash"])
        assert repo.dna_cached(**lookup, user_id="owner") is dna
        assert repo.dna_cached(**{**lookup, "analyzer_version": "pilot-2"}, user_id="owner") is None
        assert repo.dna_cached(**{**lookup, "image_hash": "f" * 64}, user_id="owner") is None
        assert repo.dna_cached(**{**lookup, "tenant_id": "tenant-b"}, user_id="owner") is None
        assert repo.dna_cached(**lookup, user_id="colleague") is None
        assert repo.dna_cached(**{**lookup, "ref_id": shared["id"]}, user_id="owner") is None


def test_explicit_analysis_enqueue_status_is_flagged_rights_checked_and_idempotent(env):
    ref = None
    # V2 remains hidden by default, even from an administrator.
    assert env.client.post(BASE + "/missing/analyze").status_code == 404
    enabled(env)
    ref = env.upload(123).json()["items"][0]["reference"]
    endpoint = BASE + "/" + ref["id"] + "/analyze"
    status_url = BASE + "/" + ref["id"] + "/analysis"
    empty = env.client.get(status_url)
    assert empty.status_code == 200
    assert empty.json()["job"] is None and not empty.json()["dna_available"]
    denied = env.client.post(endpoint)
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "typography_analysis_unavailable"
    approved = env.client.patch(BASE + "/" + ref["id"], json={
        "source_rights_status": "licensed", "editorial_status": "approved",
    })
    assert approved.status_code == 200, approved.text
    created = env.client.post(endpoint)
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["outcome"] == "created"
    assert body["analyzer_version"] == "g3-layout-font-overlap-v1"
    assert body["job"]["status"] == "queued"
    assert body["job"]["attempt_count"] == 0
    assert body["dna_available"] is False
    # Queueing is not an AI call; no new upload/blob or provider request occurs.
    assert env.storage.calls == 1
    again = env.client.post(endpoint)
    assert again.status_code == 200
    assert again.json()["outcome"] == "existing"
    assert again.json()["job"]["id"] == body["job"]["id"]
    assert env.client.get(status_url).json()["job"]["status"] == "queued"
    # Never expose lease owners, tokens, storage keys or original source URLs.
    assert not {"lease_token", "lease_owner", "remote_file_id", "source_url"} & set(body["job"])
    with Session(env.engine) as session:
        assert session.query(RrugcTypographyAnalysisJobModel).count() == 1
        audits = session.query(AuthAuditEventModel).filter_by(
            action="typography_reference.analysis_requested").all()
        assert len(audits) == 1
        assert audits[0].tenant_id == "tenant-a"
        assert "source_url" not in audits[0].detail_json

    env.identity("owner", permissions={"typography_reference.read"})
    assert env.client.get(status_url).status_code == 200
    assert env.client.post(endpoint).status_code == 403
    env.identity("colleague", permissions={"typography_reference.read"})
    assert env.client.get(status_url).status_code == 404
    env.identity("colleague", permissions={"typography_reference.manage"})
    assert env.client.post(endpoint).status_code == 200  # manager explicitly allowed
    env.identity("outsider", tenant="tenant-b", platform_admin=True)
    env.config.TYPOGRAPHY_V2_CANARY_TENANT_IDS = "tenant-a,tenant-b"
    assert env.client.get(status_url).status_code == 404
    assert env.client.post(endpoint).status_code == 404


def test_analysis_endpoint_reports_existing_versioned_dna_cache_without_ai(env):
    enabled(env)
    ref = env.upload(161).json()["items"][0]["reference"]
    assert env.client.patch(BASE + "/" + ref["id"], json={
        "source_rights_status": "owned",
    }).status_code == 200
    with Session(env.engine) as session:
        session.add(RrugcTypographyStyleDnaModel(
            tenant_id="tenant-a", reference_id=ref["id"],
            analyzer_version="g3-layout-font-overlap-v1", image_hash=ref["content_hash"],
            typography_region_json={"x": 0.1, "y": 0.2, "w": 0.7, "h": 0.5},
            style_family="serif", font_mood_json={"primary": "serif"},
            layout_graph_json={"nodes": [], "edges": []},
            overlap_rules_json=[], confidence=0.9,
        ))
        session.commit()
    result = env.client.post(BASE + "/" + ref["id"] + "/analyze")
    assert result.status_code == 200, result.text
    assert result.json()["outcome"] == "cache_hit"
    assert result.json()["dna_available"] is True
    assert result.json()["job"] is None
    with Session(env.engine) as session:
        assert session.query(RrugcTypographyAnalysisJobModel).count() == 0


def _reviewable_reference_and_dna(env, color: int = 177):
    """Synthetic authorized reference and valid AI DNA; no provider is called."""
    enabled(env)
    ref = env.upload(color).json()["items"][0]["reference"]
    assert env.client.patch(BASE + "/" + ref["id"], json={
        "source_rights_status": "owned", "editorial_status": "approved",
    }).status_code == 200
    with Session(env.engine) as session:
        dna = RrugcTypographyStyleDnaModel(
            tenant_id="tenant-a", reference_id=ref["id"],
            analyzer_version="g3-layout-font-overlap-v1",
            image_hash=ref["content_hash"],
            typography_region_json={"x": .10, "y": .15, "w": .65, "h": .55},
            style_family="arched-serif",
            font_mood_json={
                "primary": "serif", "secondary": None, "weight": "heavy",
                "contrast": "medium", "proportions": "wide",
            },
            layout_graph_json={
                "nodes": [{
                    "id": "n1", "role": "primary", "size_ratio": 1.0,
                    "path_type": "arch", "align": "center",
                }],
                "edges": [],
            },
            overlap_rules_json=[], confidence=.89,
        )
        session.add(dna)
        session.commit()
        return ref, dna.id


def test_g6_imagegen_plan_resolver_checks_current_human_dna_and_rights(env):
    from app.modules.realistic_review_ugc.typography_imagegen_pilot import (
        resolve_plan, TypographyPilotError,
    )

    ref, dna_id = _reviewable_reference_and_dna(env, 216)
    with Session(env.engine) as session:
        keyword = RrugcKeywordVolumeModel(
            tenant_id="tenant-a", keyword="Cà phê SÀI GÒN",
            keyword_normalized="g6-ca-phe-sai-gon-unique",
            picked=True, search_volume=3,
        )
        session.add(keyword)
        session.commit()
        key_id = keyword.id

    with Session(env.engine) as session:
        with pytest.raises(TypographyPilotError) as exc:
            resolve_plan(session, tenant_id="tenant-a", user_id="owner",
                         keyword_id=key_id, reference_id=ref["id"])
        assert exc.value.code == "dna_not_accepted"

    response = env.client.patch(
        BASE + "/" + ref["id"] + "/dna/" + dna_id + "/review",
        json={"decision": "accepted", "reason_code": "approved"},
    )
    assert response.status_code == 200, response.text
    with Session(env.engine) as session:
        plan = resolve_plan(
            session, tenant_id="tenant-a", user_id="owner",
            keyword_id=key_id, reference_id=ref["id"],
        )
        assert plan["skill"] == "concept-short-v2"
        assert plan["source_keyword_exact"] == "Cà phê SÀI GÒN"
        assert plan["requested_slots"] == 6
        assert plan["job_created"] is False
        with pytest.raises(TypographyPilotError):
            resolve_plan(session, tenant_id="tenant-b", user_id="owner",
                         keyword_id=key_id, reference_id=ref["id"])
        with pytest.raises(TypographyPilotError):
            resolve_plan(session, tenant_id="tenant-a", user_id="colleague",
                         keyword_id=key_id, reference_id=ref["id"])
    response = env.client.patch(
        BASE + "/" + ref["id"], json={"source_rights_status": "blocked"},
    )
    assert response.status_code == 200, response.text
    with Session(env.engine) as session:
        with pytest.raises(TypographyPilotError) as exc:
            resolve_plan(
                session, tenant_id="tenant-a", user_id="owner",
                keyword_id=key_id, reference_id=ref["id"],
            )
        assert exc.value.code == "reference_not_approved"


def test_style_lab_preview_api_requires_picked_keyword_and_accepted_private_dna(env):
    import xml.etree.ElementTree as ET

    uri = "/api/v1/realistic-review-ugc/typography/previews"
    request = {"reference_id": "private", "keyword_id": "stage0-keyword", "count": 3}
    assert env.client.post(uri, json=request).status_code == 404  # OFF
    ref, dna_id = _reviewable_reference_and_dna(env, 196)
    keyword_text = "Cà phê & Việt Nam <3"
    with Session(env.engine) as session:
        keyword = RrugcKeywordVolumeModel(
            tenant_id="tenant-a", keyword=keyword_text,
            keyword_normalized="ca phe viet nam", picked=True,
            search_volume=11,
        )
        session.add(keyword)
        session.commit()
        key_id = keyword.id
    request.update(reference_id=ref["id"], keyword_id=key_id)
    pending = env.client.post(uri, json=request)
    assert pending.status_code == 409
    assert pending.json()["detail"]["code"] == "typography_dna_not_accepted"
    accepted = env.client.patch(
        BASE + "/" + ref["id"] + "/dna/" + dna_id + "/review",
        json={"decision": "accepted", "reason_code": "approved"},
    )
    assert accepted.status_code == 200, accepted.text
    response = env.client.post(uri, json=request)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["source_keyword_exact"] == keyword_text
    assert data["job_created"] is False and data["provider_calls"] == 0
    assert data["preview_only"] is True
    assert data["quality"]["all_technical_pass"] is True
    assert data["quality"]["image_spelling_verified"] is False
    assert data["quality"]["release_ready"] is False
    assert data["quality"]["technical_pass_count"] == 3
    plan = data["generation_plan"]
    assert plan["skill"] == "concept-short-v2"
    assert plan["ready_for_imagegen"] is False
    assert plan["job_created"] is False and plan["provider_calls"] == 0
    assert plan["output_contract"]["requested_slots"] == 6
    assert plan["output_contract"]["actual_count"] == 0
    assert plan["output_contract"]["status"] == "planned"
    assert [slot["slot"] for slot in plan["slots"]] == list(range(1, 7))
    assert all(slot["source_keyword_exact"] == keyword_text for slot in plan["slots"])
    assert len({slot["layout"] for slot in plan["slots"]}) == 6
    assert len(data["items"]) == 3
    for preview in data["items"]:
        root = ET.fromstring(preview["svg"])
        rendered = "".join("".join(el.itertext()) for el in root.iter()
                           if el.tag.endswith("}text"))
        assert rendered == keyword_text
        assert preview["eligible_for_generation"] is False
        assert "<script" not in preview["svg"]
    assert env.client.post(uri, json=request).json() == data  # deterministic
    assert env.storage.calls == 1  # original reference upload only
    with Session(env.engine) as session:
        assert session.query(AuthAuditEventModel).filter_by(
            action="typography_reference.previewed",
        ).count() == 0
    env.identity("colleague", permissions={"typography_reference.read", "realistic_review_ugc.read"})
    assert env.client.post(uri, json=request).status_code == 404  # private
    env.identity("reader_only", permissions={"typography_reference.read"})
    assert env.client.post(uri, json=request).status_code == 403  # lacks keyword access
    env.identity("outsider", tenant="tenant-b")
    assert env.client.post(uri, json=request).status_code == 404  # non-canary
    env.identity("owner")
    assert env.client.post(uri, json={**request, "keyword_id": "unknown"}).status_code == 404
    assert env.client.post(uri, json={**request, "count": 4}).status_code == 422
    assert env.client.post(uri, json={**request, "prompt": "draw a cat"}).status_code == 422
    with Session(env.engine) as session:
        session.get(RrugcKeywordVolumeModel, key_id).picked = False
        session.commit()
    assert env.client.post(uri, json=request).status_code == 404


def test_style_lab_preview_is_invalidated_when_rights_are_revoked(env):
    ref, dna_id = _reviewable_reference_and_dna(env, 198)
    with Session(env.engine) as session:
        keyword = RrugcKeywordVolumeModel(
            tenant_id="tenant-a", keyword="SÀI GÒN",
            keyword_normalized="sai gon", picked=True, search_volume=5,
        )
        session.add(keyword)
        session.commit()
        key_id = keyword.id
    url = "/api/v1/realistic-review-ugc/typography/previews"
    request = {"reference_id": ref["id"], "keyword_id": key_id}
    assert env.client.patch(
        BASE + "/" + ref["id"] + "/dna/" + dna_id + "/review",
        json={"decision": "accepted", "reason_code": "approved"},
    ).status_code == 200
    assert env.client.post(url, json=request).status_code == 200
    assert env.client.patch(BASE + "/" + ref["id"], json={
        "source_rights_status": "blocked",
    }).status_code == 200
    response = env.client.post(url, json=request)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "typography_reference_not_approved"


def test_style_dna_human_review_and_structural_override_preserve_original(env):
    ref, dna_id = _reviewable_reference_and_dna(env)
    prefix = BASE + "/" + ref["id"]
    endpoint = prefix + "/dna/" + dna_id + "/review"
    original = env.client.get(prefix + "/dna").json()["items"][0]
    assert original["review"]["decision"] == "pending"
    assert original["review"]["effective_dna"] is None
    assert env.client.get(prefix + "/analysis").json()["dna_review_status"] == "pending"

    accepted = env.client.patch(endpoint, json={
        "decision": "accepted", "reason_code": "approved",
    })
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["review"]["decision"] == "accepted"
    assert accepted.json()["review"]["effective_dna"]["style_family"] == "arched-serif"
    assert env.client.get(prefix + "/analysis").json()["dna_review_status"] == "accepted"

    corrected = env.client.patch(endpoint, json={
        "decision": "accepted", "reason_code": "layout_corrected",
        "override": {
            "typography_region": {"x": .12, "y": .21, "w": .66, "h": .49},
            "font_mood": {
                "primary": "slab_serif", "secondary": None,
                "weight": "bold", "contrast": "low", "proportions": "wide",
            },
            "layout_graph": {
                "nodes": [
                    {"id": "n1", "role": "primary", "size_ratio": 1,
                     "path_type": "arch", "align": "center"},
                    {"id": "n2", "role": "secondary", "size_ratio": .45,
                     "path_type": "straight", "align": "center"},
                ],
                "edges": [{"from": "n1", "to": "n2",
                           "relation": "above", "strength": .9}],
            },
            "overlap_rules": [{
                "a": "n1", "b": "n2", "region": "center",
                "reading_priority": "primary_first",
                "max_occlusion_ratio": .18,
            }],
        },
    })
    assert corrected.status_code == 200, corrected.text
    effective = corrected.json()["review"]["effective_dna"]
    assert effective["typography_region"]["x"] == .12
    assert effective["font_mood"]["primary"] == "slab_serif"
    assert len(effective["layout_graph"]["nodes"]) == 2
    assert effective["overlap_rules"][0]["max_occlusion_ratio"] == .18
    assert corrected.json()["review"]["has_override"] is True
    with Session(env.engine) as session:
        dna = session.get(RrugcTypographyStyleDnaModel, dna_id)
        # Human edits NEVER overwrite versioned AI source DNA.
        assert dna.typography_region_json == original["typography_region"]
        assert dna.font_mood_json == original["font_mood"]
        assert dna.layout_graph_json == original["layout_graph"]
        assert dna.overlap_rules_json == original["overlap_rules"]
        assert dna.manual_override_json["image_hash"] == ref["content_hash"]
        assert "Nike" not in str(dna.manual_override_json)
        assert session.get(RrugcTypographyReferenceModel, ref["id"]).analysis_status == "ready"
        audits = session.query(AuthAuditEventModel).filter_by(
            action="typography_reference.dna_reviewed",
        ).all()
        assert len(audits) == 2 and all(row.tenant_id == "tenant-a" for row in audits)
        assert audits[-1].detail_json["structural_override"] is True
        assert "source_url" not in str(audits[-1].detail_json)
    assert env.storage.calls == 1  # never run image generation or storage writes


def test_style_dna_review_reject_near_duplicate_and_rights_recheck(env):
    ref, dna_id = _reviewable_reference_and_dna(env, 178)
    prefix = BASE + "/" + ref["id"]
    endpoint = prefix + "/dna/" + dna_id + "/review"
    flagged = env.client.patch(endpoint, json={
        "decision": "needs_review", "reason_code": "near_duplicate",
    })
    assert flagged.status_code == 200, flagged.text
    assert flagged.json()["review"]["effective_dna"] is None
    assert env.client.get(prefix + "/analysis").json()["dna_review_status"] == "needs_review"
    with Session(env.engine) as session:
        assert session.get(RrugcTypographyReferenceModel, ref["id"]).analysis_status == "needs_review"
    assert env.client.patch(prefix, json={"source_rights_status": "blocked"}).status_code == 200
    denied = env.client.patch(endpoint, json={
        "decision": "accepted", "reason_code": "approved",
    })
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "typography_rights_unverified"
    rejected = env.client.patch(endpoint, json={
        "decision": "rejected", "reason_code": "rights_concern",
    })
    assert rejected.status_code == 200
    assert rejected.json()["review"]["decision"] == "rejected"
    assert rejected.json()["review"]["effective_dna"] is None
    with Session(env.engine) as session:
        assert session.get(RrugcTypographyStyleDnaModel, dna_id).manual_override_json["decision"] == "rejected"
        assert session.query(AuthAuditEventModel).filter_by(
            action="typography_reference.dna_reviewed",
        ).count() == 2
    assert env.client.delete(prefix).status_code == 200
    assert env.client.patch(endpoint, json={
        "decision": "needs_review", "reason_code": "other",
    }).status_code == 409


def test_style_dna_review_strict_security_invalid_edit_no_tenant_leak(env):
    ref, dna_id = _reviewable_reference_and_dna(env, 179)
    endpoint = BASE + "/" + ref["id"] + "/dna/" + dna_id + "/review"
    accepted = {"decision": "accepted", "reason_code": "approved"}
    env.identity("viewer", permissions={"typography_reference.read"})
    assert env.client.patch(endpoint, json=accepted).status_code == 403
    env.identity("colleague", permissions={"typography_reference.manage"})
    assert env.client.patch(endpoint, json=accepted).status_code == 200
    env.identity("outsider", tenant="tenant-b", platform_admin=True)
    env.config.TYPOGRAPHY_V2_CANARY_TENANT_IDS = "tenant-a,tenant-b"
    assert env.client.patch(endpoint, json=accepted).status_code == 404
    assert env.client.get(BASE + "/" + ref["id"] + "/dna").status_code == 404
    env.identity("owner")
    env.config.TYPOGRAPHY_V2_ENABLED = False
    assert env.client.patch(endpoint, json=accepted).status_code == 404
    enabled(env)
    invalid = [
        {"decision": "accepted", "reason_code": "approved",
         "override": {"typography_region": {"x": .2, "y": .2, "w": .3, "h": .3}}},
        {"decision": "rejected", "reason_code": "other",
         "override": {"typography_region": {"x": .2, "y": .2, "w": .3, "h": .3}}},
        {"decision": "accepted", "reason_code": "region_corrected",
         "override": {"typography_region": {"x": .9, "y": .2, "w": .5, "h": .3}}},
        {"decision": "accepted", "reason_code": "layout_corrected",
         "override": {"layout_graph": {"nodes": [{
             "id": "BRAND NAME", "role": "primary", "size_ratio": 1,
             "path_type": "arch", "align": "center",
         }], "edges": []}}},
        {"decision": "accepted", "reason_code": "overlap_corrected",
         "override": {"overlap_rules": [{
             "a": "n1", "b": "n2", "region": "center",
             "reading_priority": "primary_first", "max_occlusion_ratio": .9,
         }]}},
        {"decision": "accepted", "reason_code": "approved",
         "source_text": "NEVER COPY THE BRAND"},
    ]
    for payload in invalid:
        response = env.client.patch(endpoint, json=payload)
        assert response.status_code == 422, (payload, response.text)
    with Session(env.engine) as session:
        dna = session.get(RrugcTypographyStyleDnaModel, dna_id)
        assert dna.manual_override_json["decision"] == "accepted"
        assert dna.manual_override_json["override"] is None
        assert session.query(AuthAuditEventModel).filter_by(
            action="typography_reference.dna_reviewed",
        ).count() == 1
    assert env.storage.calls == 1


def test_accepted_dna_is_invalidated_when_rights_are_revoked(env):
    ref, dna_id = _reviewable_reference_and_dna(env, 180)
    url = BASE + "/" + ref["id"]
    assert env.client.patch(url + "/dna/" + dna_id + "/review", json={
        "decision": "accepted", "reason_code": "approved",
    }).status_code == 200
    assert env.client.get(url + "/dna").json()["items"][0]["review"]["effective_dna"]
    assert env.client.patch(url, json={"source_rights_status": "blocked"}).status_code == 200
    review = env.client.get(url + "/dna").json()["items"][0]["review"]
    assert review["decision"] == "needs_review"
    assert review["reason_code"] == "approval_invalidated"
    assert review["effective_dna"] is None
    assert env.client.get(url + "/analysis").json()["dna_review_status"] == "needs_review"


def test_low_confidence_human_acceptance_requires_explicit_reason(env):
    ref, dna_id = _reviewable_reference_and_dna(env, 181)
    with Session(env.engine) as session:
        session.get(RrugcTypographyStyleDnaModel, dna_id).confidence = .42
        session.commit()
    endpoint = BASE + "/" + ref["id"] + "/dna/" + dna_id + "/review"
    implicit = env.client.patch(endpoint, json={
        "decision": "accepted", "reason_code": "approved",
    })
    assert implicit.status_code == 422
    assert implicit.json()["detail"]["code"] == "typography_dna_review_invalid"
    explicit = env.client.patch(endpoint, json={
        "decision": "accepted", "reason_code": "low_confidence_reviewed",
    })
    assert explicit.status_code == 200, explicit.text
    assert explicit.json()["review"]["effective_dna"]["confidence"] == .42
    with Session(env.engine) as session:
        assert session.query(AuthAuditEventModel).filter_by(
            action="typography_reference.dna_reviewed",
        ).count() == 1


def test_typography_dna_similarity_is_read_only_bounded_and_private(env):
    source, source_dna = _reviewable_reference_and_dna(env, 201)
    duplicate, duplicate_dna = _reviewable_reference_and_dna(env, 202)
    different, different_dna = _reviewable_reference_and_dna(env, 203)
    with Session(env.engine) as session:
        session.get(RrugcTypographyStyleDnaModel, source_dna).perceptual_hash = "tdh1:0000000000000000"
        session.get(RrugcTypographyStyleDnaModel, duplicate_dna).perceptual_hash = "tdh1:0000000000000000"
        session.get(RrugcTypographyStyleDnaModel, different_dna).perceptual_hash = "tdh1:ffffffffffffffff"
        # Candidate is another person's private image in the same tenant.
        session.get(RrugcTypographyReferenceModel, duplicate["id"]).created_by_user_id = "other-owner"
        session.commit()
    path = BASE + "/" + source["id"] + "/dna/" + source_dna + "/similar"
    manager = env.client.get(path, params={"max_distance": 0})
    assert manager.status_code == 200, manager.text
    assert manager.json()["status"] == "ready"
    assert manager.json()["items"] == [{
        "reference_id": duplicate["id"], "dna_id": duplicate_dna,
        "distance": 0,
    }]
    assert "perceptual_hash" not in manager.text
    assert "source_url" not in manager.text
    assert env.client.get(path, params={"max_distance": 9}).status_code == 422
    assert env.client.get(path, params={"limit": 26}).status_code == 422

    env.identity("owner", permissions={"typography_reference.read"})
    viewer = env.client.get(path)
    assert viewer.status_code == 200
    assert viewer.json()["items"] == []  # Never infer a private match.
    env.identity("owner")
    assert env.client.patch(BASE + "/" + duplicate["id"], json={
        "privacy": "workspace",
    }).status_code == 200
    env.identity("owner", permissions={"typography_reference.read"})
    shared = env.client.get(path)
    assert shared.status_code == 200
    assert len(shared.json()["items"]) == 1
    env.identity("outsider", tenant="tenant-b", platform_admin=True)
    env.config.TYPOGRAPHY_V2_CANARY_TENANT_IDS = "tenant-a,tenant-b"
    assert env.client.get(path).status_code == 404
    env.identity("owner")
    # Correcting the candidate bbox invalidates its old AI-ROI fingerprint.
    other_review = BASE + "/" + duplicate["id"] + "/dna/" + duplicate_dna + "/review"
    assert env.client.patch(other_review, json={
        "decision": "accepted", "reason_code": "region_corrected",
        "override": {"typography_region": {"x": .14, "y": .22, "w": .55, "h": .49}},
    }).status_code == 200
    assert env.client.get(path).json()["items"] == []
    # Correcting the source bbox also suppresses stale, misleading matches.
    source_review = BASE + "/" + source["id"] + "/dna/" + source_dna + "/review"
    assert env.client.patch(source_review, json={
        "decision": "accepted", "reason_code": "region_corrected",
        "override": {"typography_region": {"x": .14, "y": .22, "w": .55, "h": .49}},
    }).status_code == 200
    assert env.client.get(path).json() == {
        "status": "hash_stale_after_region_edit", "items": [],
        "scan_truncated": False,
    }
    with Session(env.engine) as session:
        session.get(RrugcTypographyStyleDnaModel, source_dna).perceptual_hash = None
        session.commit()
    assert env.client.get(path).json() == {
        "status": "hash_unavailable", "items": [], "scan_truncated": False,
    }
    assert env.storage.calls == 3  # Similarity never reads or regenerates assets.


def test_private_selection_route_fails_closed_without_pilot_or_permission(env):
    enabled(env)
    route = "/api/v1/realistic-review-ugc/typography/batches/00000000-0000-4000-8000-000000000001/selection-preview"
    request = {
        "keyword_id": "keyword-1",
        "reference_id": "reference-1",
        "selected_slots": [1],
    }
    # The Ideas/Style Lab API-only canary must not expose the private pilot.
    response = env.client.post(route, json=request)
    assert response.status_code in (403, 404)
    assert env.storage.calls == 0
    assert "private_image_path" not in response.text
    env.identity("operator-with-generate", permissions=ALL_PERMISSIONS | {"typography_generate.run"})
    response = env.client.post(route, json=request)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "typography_private_selection_disabled"
    assert env.storage.calls == 0


def test_private_selection_route_rejects_malformed_client_payload(env):
    enabled(env)
    route = "/api/v1/realistic-review-ugc/typography/batches/00000000-0000-4000-8000-000000000001/selection-preview"
    env.identity("operator-with-generate", permissions=ALL_PERMISSIONS | {"typography_generate.run"})
    for body in (
        {"keyword_id": "k", "reference_id": "r", "selected_slots": [True]},
        {"keyword_id": "k", "reference_id": "r", "selected_slots": [1.5]},
        {"keyword_id": "k", "reference_id": "r", "selected_slots": []},
        {"keyword_id": "k", "reference_id": "r", "selected_slots": [1] * 7},
        {"keyword_id": "k", "reference_id": "r", "selected_slots": [1], "asset_id": "forged"},
    ):
        response = env.client.post(route, json=body)
        assert response.status_code == 422
    assert env.storage.calls == 0
