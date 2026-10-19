"""Versioned fact-only DNA outlines; no final approval and no unverified sources."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.evidence_model import BrandListingSnapshotModel, BrandListingImageModel
from app.modules.branding_dna.knowledge_model import BrandKnowledgeDocumentModel, BrandKnowledgeChunkModel
from app.modules.branding_dna.dna_version_model import BrandDnaOutlineVersionModel, BrandDnaOutlineReviewModel
from app.modules.branding_dna.dna_version_router import (
    CreateOutlineVersion, OutlineReviewDecision, create_outline_version, list_outline_versions,
    read_outline_version, review_outline_version,
)
from app.modules.branding_dna.dna_draft_router import preview_dna_draft


def user(name="author",tenant="tenant-a"):
    return SimpleNamespace(actor_id=name, active_tenant_id=tenant)


@pytest.fixture
def db():
    engine=create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine,tables=[
        BrandShopModel.__table__,BrandListingSnapshotModel.__table__,
        BrandListingImageModel.__table__,BrandKnowledgeDocumentModel.__table__,
        BrandKnowledgeChunkModel.__table__,BrandDnaOutlineVersionModel.__table__,
        BrandDnaOutlineReviewModel.__table__,
    ])
    with Session(engine) as s:
        yield s
    engine.dispose()


def seed(db,tenant="tenant-a", name="OutlinesShop"):
    shop=BrandShopModel(
        tenant_id=tenant,platform="etsy",region="US",
        canonical_shop_key=name.lower(),storefront_url=f"https://www.etsy.com/shop/{name}",
        display_name=name,access_mode="owned",status="draft",created_by="author",
    )
    db.add(shop);db.commit()
    return shop


def add_listing(db,shop,verified=True,code="sku-001"):
    row=BrandListingSnapshotModel(
        tenant_id=shop.tenant_id,shop_id=shop.id,listing_key=code,
        source_revision="r1",export_reference="authorized-export-r1",
        title="Canvas Cap "+code,category="caps",
        source_kind="authorized_export",
        evidence_state="verified" if verified else "imported_unverified",
        coverage_state="unknown",
        captured_at=datetime(2026,10,19,8,tzinfo=timezone.utc),
        imported_by="operator",content_sha256="a"*64 if verified else "b"*64,
    )
    db.add(row);db.commit()
    return row


def fingerprint(db,shop,actor="author"):
    return preview_dna_draft(shop.id,Response(),session=db,principal=user(actor,shop.tenant_id))["input_fingerprint"]


def create(db,shop,actor="author",fp=None):
    return create_outline_version(shop.id,CreateOutlineVersion(
        input_fingerprint=fp or fingerprint(db,shop,actor)
    ),Response(),session=db,principal=user(actor,shop.tenant_id))


def review(db,shop,version,actor="reviewer",decision="fact_outline_reviewed",
           note="Checked the citation to verified metadata"):
    return review_outline_version(
        shop.id,version,OutlineReviewDecision(decision=decision,note=note),
        Response(),session=db,principal=user(actor,shop.tenant_id),
    )


def test_draft_save_blocked_without_verified_evidence(db):
    shop=seed(db)
    add_listing(db,shop,verified=False)
    with pytest.raises(HTTPException) as exc:
        create(db,shop)
    assert exc.value.status_code == 409
    assert db.query(BrandDnaOutlineVersionModel).count() == 0
    assert db.query(BrandListingSnapshotModel).one().evidence_state == "imported_unverified"


def test_version_creation_idempotency_preserves_fact_only_outline(db):
    shop=seed(db)
    listing=add_listing(db,shop)
    first=create(db,shop)
    second=create(db,shop)
    assert first["version_number"] == 1 and first["replayed"] is False
    assert second["id"] == first["id"] and second["replayed"] is True
    assert db.query(BrandDnaOutlineVersionModel).count()==1
    assert first["is_final_brand_dna"] is False
    detail=read_outline_version(shop.id,first["id"],Response(),session=db,principal=user())
    assert detail["status"]=="current"
    assert detail["outline"]["verified_product_examples"][0]["citation"] == f"listing:{listing.id}"
    assert detail["outline"]["model_invoked"] is False
    assert detail["outline"]["final_brand_dna"] is False
    assert detail["outline"]["image_rights_verified"] is False
    assert "brand_best_seller_claims" in detail["outline"]["unresolved_sections"]
    assert detail["content_sha256"] and len(detail["content_sha256"])==64


def test_stale_fingerprint_must_refresh_and_version_remains_immutable(db):
    shop=seed(db);add_listing(db,shop)
    old_fp=fingerprint(db,shop)
    first=create(db,shop)
    add_listing(db,shop,code="sku-002")
    with pytest.raises(HTTPException) as exc:
        create(db,shop,fp=old_fp)
    assert exc.value.status_code==409
    old=read_outline_version(shop.id,first["id"],Response(),session=db,principal=user())
    assert old["status"]=="stale"
    assert len(old["outline"]["verified_product_examples"])==1
    with pytest.raises(HTTPException) as exc:
        review(db,shop,first["id"])
    assert exc.value.status_code==409
    second=create(db,shop)
    assert second["version_number"]==2
    assert second["id"]!=first["id"]
    overview=list_outline_versions(shop.id,Response(),session=db,principal=user())
    assert overview["count"]==2
    assert [x["status"] for x in overview["items"]]==["current","stale"]


def test_review_requires_independent_reviewer_and_keeps_history(db):
    shop=seed(db);add_listing(db,shop)
    version=create(db,shop)
    with pytest.raises(HTTPException) as exc:
        review(db,shop,version["id"],actor="author")
    assert exc.value.status_code==403
    first=review(db,shop,version["id"])
    replay=review(db,shop,version["id"])
    assert first["replayed"] is False and replay["replayed"] is True
    assert first["id"]==replay["id"]
    second=review(db,shop,version["id"],decision="needs_changes",
                  note="Missing adequate cross-reference")
    assert first["id"]!=second["id"]
    assert db.query(BrandDnaOutlineReviewModel).count()==2
    detail=read_outline_version(shop.id,version["id"],Response(),session=db,principal=user())
    assert detail["review"]["decision"]=="needs_changes"
    assert detail["is_final_brand_dna"] is False
    assert detail["dna_generation_enabled"] is False


def test_tenant_shop_scope_nonexistent_not_accessible(db):
    a=seed(db)
    b=seed(db,tenant="tenant-b",name="OtherOutlines")
    add_listing(db,a);add_listing(db,b)
    item=create(db,a)
    for tenant,shopid,versionid in [
        ("tenant-b",a.id,item["id"]),
        ("tenant-a",b.id,item["id"]),
        ("tenant-b",b.id,item["id"]),
    ]:
        if tenant=="tenant-b" and shopid==b.id:
            # Cross-tenant version ID must not leak through an otherwise visible shop.
            with pytest.raises(HTTPException) as exc:
                read_outline_version(shopid,versionid,Response(),session=db,principal=user("reader",tenant))
        else:
            with pytest.raises(HTTPException) as exc:
                read_outline_version(shopid,versionid,Response(),session=db,principal=user("reader",tenant))
        assert exc.value.status_code==404
    with pytest.raises(HTTPException) as exc:
        list_outline_versions(a.id,Response(),session=db,principal=user("reader","tenant-b"))
    assert exc.value.status_code==404


def test_invalid_payload_and_tampered_storage_are_rejected(db):
    shop=seed(db);add_listing(db,shop)
    version=create(db,shop)
    with pytest.raises(ValidationError):
        CreateOutlineVersion(input_fingerprint="unsafe")
    with pytest.raises(ValidationError):
        OutlineReviewDecision(decision="approve_final_brand",note="Not allowed")
    row=db.get(BrandDnaOutlineVersionModel,version["id"])
    row.outline_json='{"falsified":true}';db.commit()
    with pytest.raises(HTTPException) as exc:
        read_outline_version(shop.id,version["id"],Response(),session=db,principal=user())
    assert exc.value.status_code==409
