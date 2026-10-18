"""BD-02 operator readiness is read-only, tenant-scoped and fail-closed."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.branding_dna.evidence_model import BrandListingImageModel, BrandListingSnapshotModel
from app.modules.branding_dna.evidence_router import GalleryImport, ListingImport, import_listing
from app.modules.branding_dna.model import BrandShopModel, now_utc
from app.modules.branding_dna.knowledge_model import BrandKnowledgeDocumentModel
from app.modules.branding_dna.scan_authorization_model import BrandScanAuthorizationReviewModel
from app.modules.branding_dna.scan_preflight_service import shop_identity_digest
from app.modules.branding_dna.router import ShopCreate, add_shop
from app.modules.branding_dna.scout_session_model import BrandScoutSessionModel
from app.modules.branding_dna.task_model import BrandScanTaskModel
from app.modules.branding_dna.task_router import TaskStage, scan_readiness, stage_task
from app.modules.realistic_review_ugc.model import RrugcScoutAgentModel

def who(tenant="tenant-a", *, admin=True):
    return SimpleNamespace(
        active_tenant_id=tenant, actor_id="operator",
        effective_permissions=frozenset(
            {"ai_operations.read", "ai_provider.configure"} if admin
            else {"ai_operations.read"}),
        platform_admin=False,
    )


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        BrandShopModel.__table__, BrandListingSnapshotModel.__table__,
        BrandListingImageModel.__table__, BrandScanTaskModel.__table__,
        RrugcScoutAgentModel.__table__, BrandScoutSessionModel.__table__,
        BrandKnowledgeDocumentModel.__table__, BrandScanAuthorizationReviewModel.__table__,
    ])
    with Session(engine) as session:
        yield session
    engine.dispose()


def make_shop(db, tenant="tenant-a", name="ScanShop", platform="etsy", access="authorized"):
    url = (f"https://www.etsy.com/shop/{name}" if platform == "etsy" else
           f"https://www.amazon.com/stores/{name}")
    return add_shop(ShopCreate(
        platform=platform, storefront_url=url, access_mode=access,
    ), session=db, principal=who(tenant))["id"]


def make_observation(db, shop_id, tenant="tenant-a"):
    imported = import_listing(shop_id, ListingImport(
        listing_key="item-1", source_revision="r1", export_reference="export-1",
        title="One Product", category="hat", captured_at=datetime(2026, 10, 17, tzinfo=timezone.utc),
        images=[GalleryImport(sequence_index=0, asset_sha256="a"*64, source_url_sha256="b"*64)],
    ), session=db, principal=who(tenant))
    return imported["item"]["id"]


def test_readiness_reports_real_data_but_does_not_dispatch_or_verify(db):
    shop_id = make_shop(db)
    snapshot_id = make_observation(db, shop_id)
    staged = stage_task(shop_id, TaskStage(snapshot_id=snapshot_id), db, who())["item"]
    scout = RrugcScoutAgentModel(
        tenant_id="tenant-a", name="Connected Scout", active=True,
        status="offline", token_hash="a"*64, created_by_user_id="operator",
    )
    db.add(scout)
    db.flush()
    db.add(BrandScoutSessionModel(
        agent_id=scout.id, tenant_id="tenant-a", version="0.2.5",
        shop_count=1, last_seen_at=now_utc(),
    ))
    db.commit()
    before = (db.get(BrandScanTaskModel, staged["id"]).status,
              db.get(BrandListingSnapshotModel, snapshot_id).evidence_state)
    response = Response()
    state = scan_readiness(shop_id, response, db, who())
    assert response.headers["cache-control"] == "no-store"
    assert state["paired_agents_online"] == 1
    assert state["task_counts"]["staged"] == 1
    assert state["listing_snapshot_count"] == 1
    assert state["verified_listing_count"] == 0
    assert state["scan_enabled"] is False
    assert state["dispatchable"] is False
    assert state["active_scan_jobs"] == 0
    assert state["marketplace_authorization_verified"] is False
    assert state["policy_review"]["state"] == "not_submitted"
    assert state["policy_review"]["dispatchable"] is False
    assert "etsy_written_permission_not_verified" in state["blockers"]
    assert "scan_task_dispatch_not_deployed" in state["blockers"]
    assert "human_policy_review_not_submitted" in state["blockers"]
    assert before == (db.get(BrandScanTaskModel, staged["id"]).status,
                      db.get(BrandListingSnapshotModel, snapshot_id).evidence_state)


def test_readiness_does_not_leak_tenant_data(db):
    shop_a = make_shop(db)
    shop_b = make_shop(db, tenant="tenant-b", name="OtherShop")
    make_observation(db, shop_a)
    make_observation(db, shop_b, tenant="tenant-b")
    with pytest.raises(HTTPException) as denied:
        scan_readiness(shop_a, Response(), db, who("tenant-b"))
    assert denied.value.status_code == 404
    own = scan_readiness(shop_b, Response(), db, who("tenant-b"))
    assert own["listing_snapshot_count"] == 1
    assert own["paired_agents_online"] == 0
    assert "paired_agent_not_online" in own["blockers"]




def add_review(db, shop_id, *, tenant="tenant-a", status="human_reviewed",
               expiration_days=10, doc_status="approved"):
    shop = db.get(BrandShopModel, shop_id)
    document = BrandKnowledgeDocumentModel(
        tenant_id=tenant, shop_id=shop_id, filename="policy.txt", format="txt",
        category="policy", source_note="review paperwork", content_sha256="c" * 64,
        byte_count=22, text_length=22, chunk_count=1,
        original_file=b"policy paperwork source",
        status=doc_status, uploaded_by="writer",
    )
    db.add(document)
    db.flush()
    review = BrandScanAuthorizationReviewModel(
        tenant_id=tenant, shop_id=shop_id, document_id=document.id,
        document_sha256=document.content_sha256, platform=shop.platform,
        storefront_sha256=shop_identity_digest(shop),
        policy_scope="etsy_written_access" if shop.platform == "etsy" else "amazon_agent_policy",
        status=status, submitted_by="writer", reviewed_by="reviewer",
        reviewed_at=now_utc(), review_note="Independently checked",
        expires_at=now_utc() + timedelta(days=expiration_days),
    )
    db.add(review)
    db.commit()
    return review, document


def test_readiness_distinguishes_human_review_from_provider_permission(db):
    shop_id = make_shop(db)
    review, doc = add_review(db, shop_id)
    response = Response()
    state = scan_readiness(shop_id, response, db, who())
    assert response.headers["cache-control"] == "no-store"
    assert state["policy_review"]["state"] == "current_human_review"
    assert state["policy_review"]["counts"]["current_human_review"] == 1
    assert state["policy_review"]["human_review_current"] is True
    assert "human_policy_review_not_provider_authorization" in state["blockers"]
    assert "marketplace_authorization_not_verified" in state["blockers"]
    assert state["marketplace_authorization_verified"] is False
    assert state["dispatchable"] is False and state["active_scan_jobs"] == 0
    assert "document_id" not in state["policy_review"]
    assert "submitted_by" not in str(state["policy_review"])
    viewer = scan_readiness(shop_id, Response(), db, who(admin=False))
    assert viewer["policy_review"]["state"] == "restricted"
    assert viewer["policy_review"]["counts"]["total"] == 0
    assert viewer["policy_review"]["human_review_current"] is False
    assert "human_policy_review_requires_admin" in viewer["blockers"]
    assert viewer["scan_enabled"] is False

    doc.status = "archived"
    db.commit()
    archived = scan_readiness(shop_id, Response(), db, who())
    assert archived["policy_review"]["state"] == "expired_or_changed"
    assert "human_policy_review_expired_or_changed" in archived["blockers"]

    doc.status = "approved"
    review.status = "revoked"
    db.commit()
    revoked = scan_readiness(shop_id, Response(), db, who())
    assert revoked["policy_review"]["state"] == "revoked"
    assert "human_policy_review_revoked" in revoked["blockers"]


def test_readiness_reports_pending_and_expired_not_bypassed_by_heartbeat(db):
    shop_id = make_shop(db, name="PendingShop")
    review, doc = add_review(db, shop_id, status="pending_review")
    state = scan_readiness(shop_id, Response(), db, who())
    assert state["policy_review"]["state"] == "pending_human_review"
    assert state["policy_review"]["counts"]["pending_human_review"] == 1
    assert state["dispatchable"] is False
    review.status = "human_reviewed"
    review.expires_at = now_utc() - timedelta(seconds=10)
    db.commit()
    state = scan_readiness(shop_id, Response(), db, who())
    assert state["policy_review"]["state"] == "expired_or_changed"
    assert not state["policy_review"]["human_review_current"]
    assert state["marketplace_authorization_verified"] is False


def test_readiness_policy_evidence_is_strictly_tenant_bound(db):
    shop_a = make_shop(db, name="TenantA")
    shop_b = make_shop(db, tenant="tenant-b", name="TenantB")
    add_review(db, shop_a)
    state_b = scan_readiness(shop_b, Response(), db, who("tenant-b"))
    assert state_b["policy_review"]["state"] == "not_submitted"
    assert state_b["policy_review"]["counts"]["total"] == 0
    assert state_b["scan_enabled"] is False
    add_review(db, shop_b, tenant="tenant-b")
    assert scan_readiness(shop_a, Response(), db, who())["policy_review"]["counts"]["total"] == 1
    with pytest.raises(HTTPException) as denied:
        scan_readiness(shop_b, Response(), db, who())
    assert denied.value.status_code == 404

def test_unapproved_shop_and_old_heartbeat_are_blocked(db):
    shop = make_shop(db, platform="amazon", access="public_observation")
    scout = RrugcScoutAgentModel(
        tenant_id="tenant-a", name="Old Scout", active=True,
        status="offline", token_hash="a"*64, created_by_user_id="operator",
    )
    db.add(scout)
    db.flush()
    db.add(BrandScoutSessionModel(
        agent_id=scout.id, tenant_id="tenant-a", version="0.2.5", shop_count=1,
        last_seen_at=now_utc() - timedelta(minutes=4),
    ))
    db.commit()
    state = scan_readiness(shop, Response(), db, who())
    assert state["paired_agents_online"] == 0
    assert "shop_access_not_declared" in state["blockers"]
    assert "amazon_agent_policy_review_not_verified" in state["blockers"]
    assert "paired_agent_not_online" in state["blockers"]
