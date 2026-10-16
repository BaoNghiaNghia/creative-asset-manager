"""Stateless preview + tenant-scoped durable unverified queue (not evidence)."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.handoff_model import BrandHandoffCandidateModel
from app.modules.branding_dna.router import ShopCreate, add_shop
from app.modules.branding_dna.handoff_router import (
    HandoffPacket, HandoffReviewInput, list_handoffs,
    preview_handoff, stage_handoff, update_handoff_review,
)

URL = "https://www.etsy.com/shop/ReviewCaps"
H = "a" * 64
FLAGS = {
    "manualSourceVerificationRequired": True,
    "rightsVerified": False, "trainingUseApproved": False,
    "camImportEligible": False, "evidenceCommitted": False, "scanEnabled": False,
}
CHECKS = [
    "confirm_product_and_authoritative_export",
    "confirm_marketplace_access_authorization",
    "confirm_image_and_artwork_usage_rights",
    "confirm_media_lineage_and_original_files",
]


def identity(tenant="tenant-1"):
    return SimpleNamespace(active_tenant_id=tenant, actor_id="reviewer-123")


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[BrandShopModel.__table__,
                                              BrandHandoffCandidateModel.__table__])
    with Session(engine) as session:
        yield session
    engine.dispose()


def add(db, tenant="tenant-1", access="authorized", name="ReviewCaps"):
    return add_shop(ShopCreate(platform="etsy", storefront_url=f"https://www.etsy.com/shop/{name}",
                               access_mode=access), session=db, principal=identity(tenant))


def packet(shop_url=URL):
    return {
        "schemaVersion": "brand-dna-local-handoff/v1",
        "purpose": "manual_source_and_rights_verification_only",
        "platform": "etsy",
        "shopUrlSha256": hashlib.sha256(shop_url.encode()).hexdigest(),
        "generatedAtUnixMs": 1792170000000,
        "candidateCount": 1,
        "sourceMode": "metadata_only_unverified_browser_pilot",
        "candidates": [{
            "platform": "etsy", "listingKey": "etsy:12345678",
            "title": "Everyday Cap", "imageReferenceCount": 2,
            "reportFile": "branding-dna-20261016-120000Z-abc12345.json",
            "reportSha256": H, "itemIndex": 0,
            "operatorNote": "Check seller evidence",
            "sourceKind": "browser_dom_observation",
            "sourceVerification": "unverified", "rightsState": "unknown",
            "trainingRightsState": "unknown", "salesState": "unknown",
            "requiredVerifications": CHECKS,
        }],
        **FLAGS,
    }


def model(raw=None):
    return HandoffPacket.model_validate(raw or packet())


def queue(db, shop_id, tenant="tenant-1"):
    return list_handoffs(shop_id, Response(), session=db, principal=identity(tenant),
                         limit=50, offset=0)


def test_preview_is_tenant_scoped_and_never_writes(db):
    shop = add(db)
    content = model()
    result = preview_handoff(shop["id"], content, Response(), session=db, principal=identity())
    assert result["candidate_count"] == 1
    assert result["stage_state"] == "not_staged"
    assert result["can_stage"] is True
    assert result["evidence_committed"] is False
    assert "packet_identity_not_cryptographically_attested" in result["blockers"]
    assert queue(db, shop["id"])["count"] == 0
    with pytest.raises(HTTPException) as err:
        preview_handoff(shop["id"], content, Response(), session=db, principal=identity("other"))
    assert err.value.status_code == 404


def test_stage_is_idempotent_and_review_does_not_verify_rights(db):
    shop = add(db)
    handoff = model()
    first = stage_handoff(shop["id"], handoff, Response(), session=db, principal=identity())
    assert first["created"] == 1 and first["replayed"] == 0
    second = stage_handoff(shop["id"], handoff, Response(), session=db, principal=identity())
    assert second["created"] == 0 and second["replayed"] == 1
    assert queue(db, shop["id"])["count"] == 1
    row_id = first["items"][0]["id"]
    updated = update_handoff_review(
        shop["id"], row_id, HandoffReviewInput(status="needs_authorized_export",
                                                review_note="Seller report missing"),
        Response(), session=db, principal=identity())
    assert updated["item"]["status"] == "needs_authorized_export"
    assert updated["item"]["rights_state"] == "unknown"
    assert updated["item"]["cam_import_eligible"] is False
    with pytest.raises(HTTPException) as err:
        update_handoff_review(shop["id"], row_id,
                              HandoffReviewInput(status="rejected"), Response(),
                              session=db, principal=identity("other"))
    assert err.value.status_code == 404


def test_wrong_shop_and_public_observation_are_blocked(db):
    shop = add(db, access="public_observation")
    result = preview_handoff(shop["id"], model(), Response(), session=db, principal=identity())
    assert result["can_stage"] is False
    with pytest.raises(HTTPException) as err:
        stage_handoff(shop["id"], model(), Response(), session=db, principal=identity())
    assert err.value.status_code == 403
    other = add(db, name="OtherCaps")
    with pytest.raises(HTTPException) as err:
        stage_handoff(other["id"], model(), Response(), session=db, principal=identity())
    assert err.value.status_code == 409
    assert queue(db, shop["id"])["count"] == 0


@pytest.mark.parametrize("key,value", [
    ("rightsVerified", True), ("trainingUseApproved", True),
    ("camImportEligible", True), ("evidenceCommitted", True), ("scanEnabled", True),
    ("manualSourceVerificationRequired", False),
    ("sourceMode", "verified_listing"), ("schemaVersion", "unknown"),
])
def test_unverified_flags_are_enforced(key, value):
    body = packet()
    body[key] = value
    with pytest.raises(ValidationError):
        model(body)


def test_missing_provenance_and_fake_rights_are_rejected():
    invalid = [
        {"platform": "amazon"},
        {"listingKey": "etsy:../../../etc"},
        {"sourceVerification": "verified"},
        {"rightsState": "permitted"},
        {"imageUrl": "https://example.test/image.jpg"},
        {"reportSha256": "wrong"},
        {"requiredVerifications": CHECKS[:-1]},
    ]
    for patch in invalid:
        body = packet()
        body["candidates"][0].update(patch)
        with pytest.raises(ValidationError):
            model(body)
    body = packet()
    body["candidateCount"] = 2
    with pytest.raises(ValidationError):
        model(body)


def test_staged_same_source_cannot_be_mutated_with_reused_identity(db):
    shop = add(db)
    body = packet()
    stage_handoff(shop["id"], model(body), Response(), session=db, principal=identity())
    body["candidates"][0]["title"] = "Tampered title"
    with pytest.raises(HTTPException) as err:
        stage_handoff(shop["id"], model(body), Response(), session=db, principal=identity())
    assert err.value.status_code == 409
    assert queue(db, shop["id"])["count"] == 1


def test_image_reference_count_change_rejected_on_replay(db):
    shop = add(db)
    body = packet()
    stage_handoff(shop["id"], model(body), Response(), session=db, principal=identity())
    body["candidates"][0]["imageReferenceCount"] = 3
    with pytest.raises(HTTPException) as error:
        stage_handoff(shop["id"], model(body), Response(), session=db, principal=identity())
    assert error.value.status_code == 409
    rows = queue(db, shop["id"])["items"]
    assert len(rows) == 1 and rows[0]["image_reference_count"] == 2


def test_review_cannot_set_verified_status():
    with pytest.raises(ValidationError):
        HandoffReviewInput(status="verified", review_note="I said so")
    with pytest.raises(ValidationError):
        HandoffReviewInput(status="rejected", review_note="bad\ninjected")
