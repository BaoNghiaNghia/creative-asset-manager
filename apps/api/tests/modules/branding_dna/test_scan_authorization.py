"""Manual paperwork cannot grant marketplace access by itself."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel, now_utc
from app.modules.branding_dna.router import ShopCreate, add_shop
from app.modules.branding_dna.knowledge_model import BrandKnowledgeDocumentModel
from app.modules.branding_dna.scan_authorization_model import BrandScanAuthorizationReviewModel
from app.modules.branding_dna.scan_authorization_router import (
    SubmitPolicyReview, DecidePolicyReview, submit_scan_policy,
    list_scan_policies, decide_scan_policy,
)


def who(tenant="tenant-a", actor="submitter"):
    return SimpleNamespace(active_tenant_id=tenant, actor_id=actor)


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        BrandShopModel.__table__, BrandKnowledgeDocumentModel.__table__,
        BrandScanAuthorizationReviewModel.__table__,
    ])
    with Session(engine) as session:
        yield session
    engine.dispose()


def setup_shop(db, *, tenant="tenant-a", name="TestStore", platform="etsy", category="policy",
               status="approved", access="authorized"):
    url = (f"https://www.etsy.com/shop/{name}" if platform=="etsy"
           else f"https://www.amazon.com/stores/{name}")
    shop = add_shop(ShopCreate(platform=platform, storefront_url=url,
                              access_mode=access), db, who(tenant))
    doc = BrandKnowledgeDocumentModel(
        tenant_id=tenant, shop_id=shop["id"], filename="vendor-permission.txt",
        format="txt", category=category, source_note="Submitted for manual inspection",
        content_sha256="a"*64, byte_count=17, text_length=17, chunk_count=1,
        original_file=b"review metadata!!", status=status, uploaded_by="uploader",
    )
    db.add(doc)
    db.commit()
    return shop, doc


def submit(db, shop, doc, *, tenant="tenant-a", actor="submitter",
           expires=None):
    return submit_scan_policy(
        shop["id"],
        SubmitPolicyReview(document_id=UUID(doc.id),
                           expires_at=expires or (now_utc() + timedelta(days=90))),
        Response(), db, who(tenant, actor),
    )


def decide(db, shop, item, action, actor="reviewer", note="Independent human inspection completed"):
    return decide_scan_policy(
        shop["id"], UUID(item["id"]),
        DecidePolicyReview(action=action, note=note), Response(),
        db, who(actor=actor),
    )


def test_two_person_policy_review_revoke_and_independent_external_gate(db):
    shop, doc = setup_shop(db)
    inserted = submit(db, shop, doc)
    item = inserted["item"]
    assert item["status"] == "pending_review" and item["human_review_current"] is False
    assert item["policy_scope"] == "etsy_written_access"
    assert item["marketplace_authorization_verified"] is False
    assert inserted["review_only"] is True
    with pytest.raises(HTTPException) as same:
        decide(db, shop, item, "acknowledge", actor="submitter")
    assert same.value.status_code == 403
    assert db.get(BrandScanAuthorizationReviewModel, item["id"]).status == "pending_review"
    approved = decide(db, shop, item, "acknowledge")["item"]
    assert approved["human_review_current"] is True
    assert approved["marketplace_authorization_verified"] is False
    assert approved["dispatchable"] is False and approved["scan_enabled"] is False
    rows = list_scan_policies(shop["id"], Response(), db, who())["items"]
    assert len(rows) == 1 and rows[0]["human_review_current"]
    assert rows[0]["document_sha256"] == doc.content_sha256
    revoked = decide(db, shop, item, "revoke", actor="security")["item"]
    assert revoked["status"] == "revoked" and revoked["human_review_current"] is False
    assert revoked["reviewed_by"] == "reviewer"
    assert revoked["revoked_by"] == "security"
    assert revoked["revocation_note"]
    with pytest.raises(HTTPException) as replay:
        decide(db, shop, item, "acknowledge", actor="another")
    assert replay.value.status_code == 409


def test_shop_or_policy_document_changed_invalidates_review(db):
    shop, doc = setup_shop(db)
    item = submit(db, shop, doc)["item"]
    decide(db, shop, item, "acknowledge")
    model = db.get(BrandShopModel, shop["id"])
    model.access_mode = "public_observation"
    db.commit()
    assert list_scan_policies(shop["id"], Response(), db, who())["items"][0]["human_review_current"] is False
    model.access_mode = "authorized"
    db.commit()
    doc.status = "archived"
    db.commit()
    assert list_scan_policies(shop["id"], Response(), db, who())["items"][0]["human_review_current"] is False
    doc.status = "approved"
    model.storefront_url = "https://www.etsy.com/shop/Changed"
    db.commit()
    assert list_scan_policies(shop["id"], Response(), db, who())["items"][0]["human_review_current"] is False


def test_reviewer_rechecks_doc_and_expiry_at_decision(db):
    shop, doc = setup_shop(db)
    item = submit(db, shop, doc)["item"]
    doc.status = "rejected"
    db.commit()
    with pytest.raises(HTTPException) as failed:
        decide(db, shop, item, "acknowledge")
    assert failed.value.status_code == 409
    doc.status = "approved"
    row = db.get(BrandScanAuthorizationReviewModel, item["id"])
    row.expires_at = now_utc() - timedelta(seconds=1)
    db.commit()
    with pytest.raises(HTTPException) as expired:
        decide(db, shop, item, "acknowledge")
    assert expired.value.status_code == 409


def test_cross_tenant_document_and_shop_isolation(db):
    shop_a, doc_a = setup_shop(db)
    shop_b, doc_b = setup_shop(db, tenant="tenant-b", name="OtherStore")
    with pytest.raises(HTTPException) as miss:
        submit(db, shop_a, doc_b)
    assert miss.value.status_code == 409
    with pytest.raises(HTTPException) as foreign:
        submit(db, shop_a, doc_a, tenant="tenant-b")
    assert foreign.value.status_code == 404
    with pytest.raises(HTTPException) as read:
        list_scan_policies(shop_a["id"], Response(), db, who("tenant-b"))
    assert read.value.status_code == 404
    item = submit(db, shop_a, doc_a)["item"]
    with pytest.raises(HTTPException) as fake:
        decide_scan_policy(shop_b["id"], UUID(item["id"]),
                           DecidePolicyReview(action="acknowledge",note="Not my shop at all"),
                           Response(),db,who("tenant-b","other"))
    assert fake.value.status_code == 404


def test_no_self_declared_shop_permission_or_unapproved_docs(db):
    shop, doc = setup_shop(db, access="public_observation")
    with pytest.raises(HTTPException) as denied:
        submit(db, shop, doc)
    assert denied.value.status_code == 409
    other, other_doc = setup_shop(db, name="CategoryStore", category="other")
    with pytest.raises(HTTPException) as kind:
        submit(db, other, other_doc)
    assert kind.value.status_code == 409
    doc.status = "approved"
    shop_model = db.get(BrandShopModel, shop["id"])
    shop_model.access_mode = "authorized"
    db.commit()
    assert submit(db, shop, doc)["item"]["status"] == "pending_review"


def test_submission_requires_expiry_and_document_uniqueness(db):
    shop, doc = setup_shop(db,platform="amazon")
    with pytest.raises(HTTPException) as naive:
        submit(db,shop,doc,expires=datetime(2026,12,1))
    assert naive.value.status_code == 422
    with pytest.raises(HTTPException) as too_far:
        submit(db,shop,doc,expires=now_utc()+timedelta(days=366))
    assert too_far.value.status_code == 422
    inserted=submit(db,shop,doc)
    assert inserted["item"]["policy_scope"] == "amazon_agent_policy"
    with pytest.raises(HTTPException) as duplicate:
        submit(db,shop,doc)
    assert duplicate.value.status_code == 409
