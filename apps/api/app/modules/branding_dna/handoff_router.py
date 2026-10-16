"""Validate and stage local Scout Manager handoff candidates, never verified evidence.

Claims from a browser pilot and even its SHA-256 are untrusted user input.
The SHA only links claimed source revisions; this API cannot attest that a
listing was observed or that the operator has rights to use its images.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.branding_dna.evidence_router import shop_for_tenant
from app.modules.branding_dna.handoff_model import BrandHandoffCandidateModel
from app.modules.branding_dna.model import now_utc

router = APIRouter(prefix="/api/v1/branding-dna", tags=["branding-dna-handoff"])
logger = logging.getLogger(__name__)
READ = require_permission("ai_operations.read")
CONFIGURE = require_permission("ai_provider.configure")

HEX = r"^[a-f0-9]{64}$"
REPORT_FILE = r"^branding-dna-[A-Za-z0-9.-]{2,95}\.json$"
LISTING_KEY = r"^(?:etsy:[0-9]{1,20}|amazon:[A-Z0-9]{10})$"
REQUIRED_CHECKS = (
    "confirm_product_and_authoritative_export",
    "confirm_marketplace_access_authorization",
    "confirm_image_and_artwork_usage_rights",
    "confirm_media_lineage_and_original_files",
)


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", alias_generator=lambda name: "".join(
        part.capitalize() if index else part for index, part in enumerate(name.split("_"))
    ))


class HandoffCandidate(StrictPayload):
    platform: Literal["amazon", "etsy"]
    listing_key: str = Field(min_length=6, max_length=60, pattern=LISTING_KEY)
    title: str = Field(min_length=2, max_length=180)
    image_reference_count: int = Field(ge=0, le=12)
    report_file: str = Field(min_length=22, max_length=110, pattern=REPORT_FILE)
    report_sha256: str = Field(pattern=HEX)
    item_index: int = Field(ge=0, le=4)
    operator_note: str = Field(default="", max_length=200)
    source_kind: Literal["browser_dom_observation"]
    source_verification: Literal["unverified"]
    rights_state: Literal["unknown"]
    training_rights_state: Literal["unknown"]
    sales_state: Literal["unknown"]
    required_verifications: tuple[str, str, str, str]

    @model_validator(mode="after")
    def check_candidate(self):
        if tuple(self.required_verifications) != REQUIRED_CHECKS:
            raise ValueError("The four independent verification gates are required")
        if self.listing_key.split(":")[0] != self.platform:
            raise ValueError("Listing key does not match candidate platform")
        if self.title != self.title.strip() or any(ord(c) < 32 for c in self.title):
            raise ValueError("Invalid title")
        if any(ord(c) < 32 for c in self.operator_note):
            raise ValueError("Review note must be a single line")
        return self


class HandoffPacket(StrictPayload):
    schema_version: Literal["brand-dna-local-handoff/v1"]
    purpose: Literal["manual_source_and_rights_verification_only"]
    platform: Literal["amazon", "etsy"]
    shop_url_sha256: str = Field(pattern=HEX)
    generated_at_unix_ms: int = Field(ge=1_577_836_800_000, le=4_102_444_800_000)
    candidate_count: int = Field(ge=1, le=40)
    source_mode: Literal["metadata_only_unverified_browser_pilot"]
    candidates: list[HandoffCandidate] = Field(min_length=1, max_length=40)
    manual_source_verification_required: Literal[True]
    rights_verified: Literal[False]
    training_use_approved: Literal[False]
    cam_import_eligible: Literal[False]
    evidence_committed: Literal[False]
    scan_enabled: Literal[False]

    @model_validator(mode="after")
    def validate_packet(self):
        if self.candidate_count != len(self.candidates):
            raise ValueError("Candidate count does not match packet contents")
        if any(candidate.platform != self.platform for candidate in self.candidates):
            raise ValueError("Mixed marketplace packets are not accepted")
        ids = [
            (item.report_sha256, item.report_file, item.item_index, item.listing_key)
            for item in self.candidates
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate source listings in handoff")
        return self


def check_shop_and_packet(session: Session, tenant_id: str, shop_id: str, packet: HandoffPacket):
    shop = shop_for_tenant(session, tenant_id, shop_id)
    if shop.platform != packet.platform:
        raise HTTPException(409, detail="Handoff platform does not match selected shop")
    expected = hashlib.sha256(shop.storefront_url.encode("utf-8")).hexdigest()
    if packet.shop_url_sha256 != expected:
        raise HTTPException(409, detail="Shop URL hash mismatch; select the exact shop registration")
    if shop.platform not in ("amazon", "etsy"):
        raise HTTPException(409, detail="Only Amazon/Etsy browser pilot handoffs supported")
    return shop


def canonical_packet_hash(packet: HandoffPacket) -> str:
    payload = json.dumps(packet.model_dump(mode="json", by_alias=True),
                         sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def candidate_key(item: HandoffCandidate, shop_id: str) -> str:
    parts = [shop_id, item.report_file, item.report_sha256,
             str(item.item_index), item.listing_key]
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def present(item: BrandHandoffCandidateModel) -> dict:
    return {
        "id": item.id, "shop_id": item.shop_id,
        "listing_key": item.listing_key, "title": item.title,
        "image_reference_count": item.image_reference_count,
        "report_file": item.report_file, "report_sha256": item.report_sha256,
        "item_index": item.item_index, "operator_note": item.operator_note,
        "status": item.status, "review_note": item.review_note,
        "created_at": item.created_at.isoformat(),
        "reviewed_at": item.reviewed_at.isoformat() if item.reviewed_at else None,
        "source_verification": "unverified", "rights_state": "unknown",
        "training_rights_state": "unknown", "cam_import_eligible": False,
    }


def preview_payload(shop_id: str, packet: HandoffPacket, *, session: Session, principal: CurrentPrincipal):
    shop = check_shop_and_packet(session, principal.active_tenant_id, shop_id, packet)
    return {
        "shop_id": shop.id, "platform": shop.platform,
        "candidate_count": packet.candidate_count,
        "packet_sha256": canonical_packet_hash(packet),
        "candidates": [{
            "listing_key": item.listing_key, "title": item.title,
            "image_reference_count": item.image_reference_count,
            "report_sha256": item.report_sha256,
            "operator_note": item.operator_note,
            "review_state": "unverified_browser_shortlist",
        } for item in packet.candidates],
        "can_stage": shop.access_mode in ("owned", "authorized"),
        "blockers": [
            "packet_identity_not_cryptographically_attested",
            "authoritative_export_not_verified",
            "platform_authorization_not_independently_verified",
            "image_and_artwork_rights_unknown",
            "original_media_lineage_unknown",
        ],
        "stage_state": "not_staged",
        "evidence_committed": False, "training_use_approved": False,
        "scan_enabled": False,
    }


@router.post("/shops/{shop_id}/handoffs/preview")
def preview_handoff(
    shop_id: str, packet: HandoffPacket, response: Response,
    session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(READ),
):
    result = preview_payload(shop_id, packet, session=session, principal=principal)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/shops/{shop_id}/handoffs/stage", status_code=201)
def stage_handoff(
    shop_id: str, packet: HandoffPacket, response: Response,
    session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(CONFIGURE),
):
    shop = check_shop_and_packet(session, principal.active_tenant_id, shop_id, packet)
    if shop.access_mode not in ("owned", "authorized"):
        raise HTTPException(403, detail="Only owned/authorized shops may stage handoff candidates")
    digest = canonical_packet_hash(packet)
    now = now_utc()
    rows = []
    created = 0
    for item in packet.candidates:
        identity = candidate_key(item, shop_id)
        existing = session.scalar(select(BrandHandoffCandidateModel).where(
            BrandHandoffCandidateModel.tenant_id == principal.active_tenant_id,
            BrandHandoffCandidateModel.shop_id == shop_id,
            BrandHandoffCandidateModel.candidate_sha256 == identity,
        ))
        if existing is not None:
            if (existing.listing_key != item.listing_key or existing.report_sha256 != item.report_sha256
                    or existing.report_file != item.report_file or existing.item_index != item.item_index
                    or existing.image_reference_count != item.image_reference_count
                    or existing.title != item.title or existing.operator_note != item.operator_note):
                raise HTTPException(409, detail="Source identity already staged with different content")
            rows.append(existing)
            continue
        record = BrandHandoffCandidateModel(
            tenant_id=principal.active_tenant_id, shop_id=shop_id,
            candidate_sha256=identity, packet_sha256=digest,
            report_sha256=item.report_sha256, report_file=item.report_file,
            listing_key=item.listing_key, title=item.title, item_index=item.item_index,
            image_reference_count=item.image_reference_count, operator_note=item.operator_note,
            status="pending_source_review", created_by=principal.actor_id,
            created_at=now,
        )
        session.add(record)
        rows.append(record)
        created += 1
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, detail="Concurrent handoff stage. Refresh the verification queue.") from exc
    logger.info("brand_handoff_staged tenant=%s shop=%s actor=%s created=%d replayed=%d",
                principal.active_tenant_id, shop_id, principal.actor_id, created, len(rows) - created)
    response.headers["Cache-Control"] = "no-store"
    return {
        "shop_id": shop.id, "count": len(rows), "created": created,
        "replayed": len(rows) - created, "state": "pending_source_review",
        "items": [present(row) for row in rows],
        "evidence_committed": False, "scan_enabled": False,
    }


@router.get("/shops/{shop_id}/handoffs")
def list_handoffs(
    shop_id: str, response: Response,
    limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=100000),
    session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(READ),
):
    shop_for_tenant(session, principal.active_tenant_id, shop_id)
    criteria = (
        BrandHandoffCandidateModel.tenant_id == principal.active_tenant_id,
        BrandHandoffCandidateModel.shop_id == shop_id,
    )
    total = session.scalar(select(func.count()).select_from(BrandHandoffCandidateModel).where(*criteria)) or 0
    rows = session.scalars(select(BrandHandoffCandidateModel).where(*criteria).order_by(
        BrandHandoffCandidateModel.created_at.desc(), BrandHandoffCandidateModel.id.desc(),
    ).offset(offset).limit(limit)).all()
    response.headers["Cache-Control"] = "no-store"
    return {"items": [present(row) for row in rows], "count": total, "limit": limit,
            "offset": offset, "scope": "unverified_handoff_queue", "evidence_committed": False}


class HandoffReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["pending_source_review", "needs_authorized_export", "rejected"]
    review_note: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def note_is_safe(self):
        if any(ord(c) < 32 for c in self.review_note):
            raise ValueError("Review note must be one line")
        return self


@router.patch("/shops/{shop_id}/handoffs/{candidate_id}")
def update_handoff_review(
    shop_id: str, candidate_id: str, body: HandoffReviewInput,
    response: Response,
    session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(CONFIGURE),
):
    shop_for_tenant(session, principal.active_tenant_id, shop_id)
    row = session.scalar(select(BrandHandoffCandidateModel).where(
        BrandHandoffCandidateModel.id == candidate_id,
        BrandHandoffCandidateModel.tenant_id == principal.active_tenant_id,
        BrandHandoffCandidateModel.shop_id == shop_id,
    ))
    if row is None:
        raise HTTPException(404, detail="Handoff candidate not found")
    row.status = body.status
    row.review_note = body.review_note
    row.reviewed_by = principal.actor_id
    row.reviewed_at = now_utc()
    session.commit()
    logger.info("brand_handoff_triage tenant=%s shop=%s candidate=%s actor=%s status=%s",
                principal.active_tenant_id, shop_id, candidate_id, principal.actor_id, body.status)
    response.headers["Cache-Control"] = "no-store"
    return {"item": present(row), "evidence_committed": False}
