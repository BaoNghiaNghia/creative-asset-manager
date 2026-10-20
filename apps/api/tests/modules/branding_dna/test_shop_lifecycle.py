"""Activation is a curated catalog state, never authorization to crawl."""
from types import SimpleNamespace
from fastapi import Response, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
import pytest

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.access_declaration_model import BrandShopAccessDeclarationModel
from app.modules.branding_dna.access_review_model import BrandShopAccessReviewModel
from app.modules.branding_dna.task_model import BrandScanTaskModel
from app.modules.branding_dna.evidence_model import BrandListingSnapshotModel
from app.modules.branding_dna.router import (
    add_shop, ShopCreate, update_shop_lifecycle, ShopLifecycleChange,
)
from app.modules.branding_dna.access_declaration_router import (
    declare_shop_relationship, ShopRelationshipDeclaration,
    submit_documentary_review, DocumentaryReview,
)


@pytest.fixture
def db():
    engine=create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine,tables=[
        BrandShopModel.__table__, BrandShopAccessDeclarationModel.__table__,
        BrandShopAccessReviewModel.__table__,BrandListingSnapshotModel.__table__,
        BrandScanTaskModel.__table__,
    ])
    with Session(engine) as session: yield session
    engine.dispose()


def actor(name="operator",tenant="a"):
    return SimpleNamespace(actor_id=name,active_tenant_id=tenant)


def create(db,name="PilotCaps",tenant="a"):
    return add_shop(ShopCreate(platform="etsy",
        storefront_url=f"https://www.etsy.com/shop/{name}"),
        session=db,principal=actor(tenant=tenant))["id"]


def activate(db,shop_id,doc=None,who="operator",tenant="a"):
    return update_shop_lifecycle(shop_id,ShopLifecycleChange(
        status="active",declaration_id=doc,
        acknowledge_marketplace_rights_not_verified=True,
    ),session=db,principal=actor(who,tenant))


def review_doc(db,shop_id,relationship="owned"):
    dec=declare_shop_relationship(shop_id,ShopRelationshipDeclaration(
        relationship=relationship,evidence_reference="internal-proof-2026-10-20",
    ),Response(),session=db,principal=actor("declarant"))
    submit_documentary_review(shop_id,DocumentaryReview(
        declaration_id=dec["id"],decision="documentation_noted",
        note="Documentary relationship reviewed independently, not marketplace scan permission",
    ),Response(),session=db,principal=actor("reviewer"))
    return dec["id"]


def test_activation_requires_independent_review_and_never_queues_job(db):
    sid=create(db)
    with pytest.raises(HTTPException) as exc: activate(db,sid)
    assert exc.value.status_code==409
    did=review_doc(db,sid)
    result=activate(db,sid,did)
    assert result["item"]["status"]=="active"
    assert result["item"]["access_mode"]=="owned"
    assert result["rights_verified"] is False
    assert result["scan_enabled"] is False
    assert result["dispatchable"] is False
    assert result["jobs_created"]==0
    assert db.query(BrandScanTaskModel).count()==0
    again=activate(db,sid,did)
    assert again["changed"] is False
    assert db.query(BrandScanTaskModel).count()==0


def test_activation_denies_cross_tenant_and_self_reviewer(db):
    sid=create(db)
    did=review_doc(db,sid,"authorized")
    with pytest.raises(HTTPException) as exc: activate(db,sid,did,tenant="b")
    assert exc.value.status_code==404
    with pytest.raises(HTTPException) as exc: activate(db,sid,did,who="reviewer")
    assert exc.value.status_code==409
    assert db.get(BrandShopModel,sid).status=="draft"


def test_pause_stops_active_status_without_revoking_declarations(db):
    sid=create(db)
    did=review_doc(db,sid)
    activate(db,sid,did)
    paused=update_shop_lifecycle(sid,ShopLifecycleChange(
        status="paused",acknowledge_marketplace_rights_not_verified=True,
    ),session=db,principal=actor("safety-operator"))
    assert paused["item"]["status"]=="paused"
    assert paused["item"]["access_mode"]=="owned"
    assert paused["dispatchable"] is False
    with pytest.raises(HTTPException) as exc: update_shop_lifecycle(sid,ShopLifecycleChange(
        status="paused",acknowledge_marketplace_rights_not_verified=True,
    ),session=db,principal=actor())
    assert exc.value.status_code==409


def test_self_declared_or_competitor_relationship_not_enough(db):
    sid=create(db)
    did=declare_shop_relationship(sid,ShopRelationshipDeclaration(
        relationship="owned",evidence_reference="proof-still-pending",
    ),Response(),session=db,principal=actor("declarant"))["id"]
    with pytest.raises(HTTPException) as exc: activate(db,sid,did)
    assert exc.value.status_code==409
    sid2=create(db,name="CompetitorCaps")
    comp=declare_shop_relationship(sid2,ShopRelationshipDeclaration(
        relationship="competitor_research",
    ),Response(),session=db,principal=actor("declarant"))["id"]
    with pytest.raises(HTTPException) as exc: activate(db,sid2,comp)
    assert exc.value.status_code==409
