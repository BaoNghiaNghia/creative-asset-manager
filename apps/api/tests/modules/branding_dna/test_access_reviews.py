"""A review is an append-only documentary decision, NEVER a marketplace access grant."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.access_declaration_model import BrandShopAccessDeclarationModel
from app.modules.branding_dna.access_review_model import BrandShopAccessReviewModel
from app.modules.branding_dna.task_model import BrandScanTaskModel
from app.modules.branding_dna.evidence_model import BrandListingSnapshotModel
from app.modules.branding_dna.router import add_shop, ShopCreate
from app.modules.branding_dna.access_declaration_router import (
    ShopRelationshipDeclaration, DocumentaryReview,
    declare_shop_relationship, list_access_reviews, submit_documentary_review,
    read_shop_scan_readiness,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        BrandShopModel.__table__, BrandShopAccessDeclarationModel.__table__,
        BrandShopAccessReviewModel.__table__, BrandScanTaskModel.__table__, BrandListingSnapshotModel.__table__,
    ])
    with Session(engine) as session:
        yield session
    engine.dispose()


def actor(name="declarant", tenant="tenant-a"):
    return SimpleNamespace(active_tenant_id=tenant, actor_id=name)


def shop(db, *, tenant="tenant-a", name="ReviewShop"):
    return add_shop(
        ShopCreate(platform="etsy", storefront_url=f"https://www.etsy.com/shop/{name}"),
        principal=actor(tenant=tenant), session=db,
    )["id"]


def declaration(db, shop_id, *, who="declarant", tenant="tenant-a", ref="consent-doc-1234"):
    return declare_shop_relationship(
        shop_id,
        ShopRelationshipDeclaration(relationship="authorized", evidence_reference=ref),
        Response(), session=db, principal=actor(who, tenant),
    )


def review(db, shop_id, declaration_id, decision="needs_documentation",
           note="Missing dated permission document", *, who="reviewer", tenant="tenant-a"):
    return submit_documentary_review(
        shop_id,
        DocumentaryReview(declaration_id=declaration_id, decision=decision, note=note),
        Response(), session=db, principal=actor(who, tenant),
    )


def test_documentation_review_does_not_grant_rights_or_claim_task(db):
    sid = shop(db)
    d = declaration(db, sid)
    task = BrandScanTaskModel(
        tenant_id="tenant-a", shop_id=sid, snapshot_id=str(uuid4()),
        status="staged", created_by="declarant",
    )
    db.add(task)
    db.commit()
    original = (task.status, task.lease_id, task.agent_id, task.lease_generation)
    doc = review(db, sid, d["id"], decision="documentation_noted",
                 note="Contract found; marketplace rights are not yet verified")
    assert doc["rights_verified"] is False
    assert doc["dispatchable"] is False
    assert doc["replayed"] is False
    b = read_shop_scan_readiness(Response(), session=db, principal=actor("viewer"))
    assert b["ready_count"] == 0
    assert b["scan_enabled"] is False
    assert "shop_access_reviewed_pending_marketplace_authorization" in b["items"][0]["blockers"]
    assert "marketplace_authorization_not_verified" in b["items"][0]["blockers"]
    saved = db.get(BrandShopModel, sid)
    assert saved.access_mode == "public_observation" and saved.status == "draft"
    db.refresh(task)
    assert (task.status, task.lease_id, task.agent_id, task.lease_generation) == original


@pytest.mark.parametrize("decision,code", [
    ("needs_documentation", "shop_access_needs_documentation"),
    ("rejected", "shop_access_documentation_rejected"),
    ("documentation_noted", "shop_access_reviewed_pending_marketplace_authorization"),
])
def test_review_blocker_codes_and_idempotency(db, decision, code):
    sid = shop(db)
    d = declaration(db, sid)
    first = review(db, sid, d["id"], decision=decision)
    again = review(db, sid, d["id"], decision=decision)
    assert first["id"] == again["id"]
    assert again["replayed"] is True
    assert db.query(BrandShopAccessReviewModel).count() == 1
    result = read_shop_scan_readiness(Response(), session=db, principal=actor("reader"))
    assert code in result["items"][0]["blockers"]
    assert result["items"][0]["review_decision"] == decision
    assert result["items"][0]["status"] == "blocked"


def test_review_history_is_append_only_and_latest_is_shown(db):
    sid = shop(db)
    d = declaration(db, sid)
    first = review(db, sid, d["id"])
    second = review(db, sid, d["id"], "documentation_noted", "Document was checked but no marketplace rights")
    assert second["id"] != first["id"]
    assert db.query(BrandShopAccessReviewModel).count() == 2
    queue = list_access_reviews(Response(), session=db, principal=actor("reviewer"))
    assert len(queue["items"]) == 1
    assert queue["items"][0]["review"]["id"] == second["id"]
    assert queue["items"][0]["rights_verified"] is False
    assert queue["scan_enabled"] is False


def test_self_review_and_old_declaration_rejected(db):
    sid = shop(db)
    first = declaration(db, sid)
    with pytest.raises(HTTPException) as exc:
        review(db, sid, first["id"], who="declarant")
    assert exc.value.status_code == 403
    newer = declaration(db, sid, ref="consent-doc-5678")
    with pytest.raises(HTTPException) as exc:
        review(db, sid, first["id"])
    assert exc.value.status_code == 409
    assert review(db, sid, newer["id"])["rights_verified"] is False


def test_cross_tenant_and_cross_shop_rejected(db):
    a = shop(db, name="TenantA")
    b = shop(db, name="TenantB", tenant="tenant-b")
    c = shop(db, name="OtherShop")
    d = declaration(db, a)
    with pytest.raises(HTTPException) as exc:
        review(db, b, d["id"], tenant="tenant-b")
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        review(db, c, d["id"])
    assert exc.value.status_code == 404
    q = list_access_reviews(Response(), session=db, principal=actor("reviewer", "tenant-b"))
    assert not q["items"]


def test_invalid_input_and_competitor_declaration_not_reviewable(db):
    sid = shop(db)
    d = declare_shop_relationship(
        sid, ShopRelationshipDeclaration(relationship="competitor_research"),
        Response(), session=db, principal=actor(),
    )
    with pytest.raises(HTTPException) as exc:
        review(db, sid, d["id"])
    assert exc.value.status_code == 409
    with pytest.raises(ValidationError):
        DocumentaryReview(declaration_id=str(uuid4()), decision="approved", note="Not permitted")
    owned = declaration(db, sid)
    with pytest.raises(HTTPException) as exc:
        review(db, sid, owned["id"], note="invalid\nline")
    assert exc.value.status_code == 422
