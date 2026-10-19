"""Audit listing metadata and rights paperwork without claiming independent proof."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.router import ShopCreate, add_shop
from app.modules.branding_dna.evidence_model import BrandListingSnapshotModel, BrandListingImageModel
from app.modules.branding_dna.evidence_assessment_model import BrandEvidenceAssessmentModel
from app.modules.branding_dna.evidence_router import ListingImport, GalleryImport, import_listing
from app.modules.branding_dna.evidence_assessment_router import (
    EvidenceAssessmentRequest, list_evidence_assessments, submit_evidence_assessment,
)


def principal(tenant="tenant-a", actor="reviewer"):
    return SimpleNamespace(active_tenant_id=tenant, actor_id=actor)


@pytest.fixture
def db():
    engine=create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine,tables=[
        BrandShopModel.__table__,BrandListingSnapshotModel.__table__,
        BrandListingImageModel.__table__,BrandEvidenceAssessmentModel.__table__,
    ])
    with Session(engine) as s:
        yield s
    engine.dispose()


def seed(db, tenant="tenant-a", shopname="ReviewEvidence"):
    sid=add_shop(
        ShopCreate(platform="etsy",storefront_url=f"https://www.etsy.com/shop/{shopname}",
                   access_mode="authorized"),session=db,principal=principal(tenant,"importer"),
    )["id"]
    result=import_listing(sid, ListingImport(
        listing_key="sku100",source_revision="r1",export_reference="authorized-export",
        title="Cap embroidered",category="hats",
        captured_at=datetime(2026,10,19,8,tzinfo=timezone.utc),
        images=[GalleryImport(sequence_index=0,asset_sha256="a"*64,source_url_sha256="b"*64)],
    ),session=db,principal=principal(tenant,"importer"))
    return sid,result["item"]["id"]


def submit(db,sid,listing,actor="reviewer",tenant="tenant-a",
           decision="metadata_consistent",rights_basis="unknown",
           evidence_reference=None,note="Compared against permitted paper export"):
    return submit_evidence_assessment(
        sid,listing,EvidenceAssessmentRequest(
            decision=decision,rights_basis=rights_basis,
            evidence_reference=evidence_reference,note=note,
        ),Response(),session=db,principal=principal(tenant,actor))


def test_assessment_never_changes_listing_or_image_rights(db):
    sid,listing=seed(db)
    result=submit(db,sid,listing,rights_basis="consent_reference_recorded",
                  evidence_reference="rights-file-2026-10")
    assert result["assessment_grants_listing_verification"] is False
    assert result["assessment_grants_image_rights"] is False
    assert result["scan_enabled"] is False and result["dispatchable"] is False
    snapshot=db.get(BrandListingSnapshotModel,listing)
    assert snapshot.evidence_state == "imported_unverified"
    assert snapshot.coverage_state == "unknown"
    images=db.query(BrandListingImageModel).all()
    assert [x.rights_state for x in images] == ["unknown"]
    assert db.query(BrandEvidenceAssessmentModel).count() == 1


def test_append_only_history_idempotent_replay_and_tenant_scope(db):
    sid,listing=seed(db)
    first=submit(db,sid,listing)
    replay=submit(db,sid,listing)
    assert replay["replayed"] is True and replay["id"] == first["id"]
    next_state=submit(db,sid,listing,decision="rejected",
                      note="Metadata diverges from the owner source")
    assert next_state["id"] != first["id"]
    assert db.query(BrandEvidenceAssessmentModel).count() == 2
    response=Response()
    history=list_evidence_assessments(
        sid,listing,response,session=db,principal=principal())
    assert response.headers["Cache-Control"]=="no-store"
    assert len(history["items"])==2
    assert {x["id"] for x in history["items"]}=={first["id"],next_state["id"]}
    assert all(x["assessment_grants_image_rights"] is False for x in history["items"])
    other_sid,other_listing=seed(db,tenant="tenant-b",shopname="OtherEvidenceShop")
    for shopid,lid,tenant in [(sid,listing,"tenant-b"),(other_sid,other_listing,"tenant-a"),
                              (other_sid,listing,"tenant-b")]:
        with pytest.raises(HTTPException) as err:
            list_evidence_assessments(
                shopid,lid,Response(),session=db,principal=principal(tenant))
        assert err.value.status_code == 404
        with pytest.raises(HTTPException) as err:
            submit(db,shopid,lid,tenant=tenant)
        assert err.value.status_code == 404


def test_self_review_rejected_and_controls_not_accepted(db):
    sid,listing=seed(db)
    with pytest.raises(HTTPException) as err:
        submit(db,sid,listing,actor="importer")
    assert err.value.status_code == 403
    for values in [
        {"decision":"approved","rights_basis":"unknown","note":"Not permitted"},
        {"decision":"metadata_consistent","rights_basis":"consent_reference_recorded",
         "note":"No document reference"},
        {"decision":"metadata_consistent","rights_basis":"unknown",
         "evidence_reference":"rights-contract-1","note":"Misattributed reference"},
        {"decision":"metadata_consistent","rights_basis":"unknown",
         "note":"invalid\ncontrol"},
    ]:
        with pytest.raises(ValidationError):
            EvidenceAssessmentRequest(**values)
    assert db.query(BrandEvidenceAssessmentModel).count() == 0
