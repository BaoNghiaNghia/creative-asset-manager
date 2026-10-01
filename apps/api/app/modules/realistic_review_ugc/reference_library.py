from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel,
    RrugcCandidateModel,
    RrugcReferenceAssetModel,
)
from app.modules.realistic_review_ugc.repository import RrugcRepository


REFERENCE_PROFILE_REALISTIC_PERSON_UGC = "realistic-person-ugc"


class ReferenceLibraryError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class ReferenceAssetPromotion:
    asset: RrugcReferenceAssetModel
    created: bool


def _reference_type(candidate: RrugcCandidateModel) -> str:
    if int(candidate.people_count or 0) > 0:
        return "person"
    return "other"


def _context_score(candidate: RrugcCandidateModel) -> float | None:
    signal = candidate.ai_signal_json if isinstance(candidate.ai_signal_json, dict) else {}
    match = signal.get("context_match")
    if not isinstance(match, dict):
        return None
    value = match.get("score")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class RrugcReferenceLibrary:
    """Generic durable reference layer shared by Pinterest and future uploads."""

    def __init__(self, session: Session):
        self.session = session
        self.repository = RrugcRepository(session)

    def promote_pinterest_candidate(
        self,
        *,
        tenant_id: str,
        user_id: str,
        campaign: RrugcCampaignModel,
        candidate: RrugcCandidateModel,
    ) -> ReferenceAssetPromotion:
        if campaign.tenant_id != tenant_id or candidate.tenant_id != tenant_id:
            raise ReferenceLibraryError(
                "reference_asset_scope_mismatch",
                "Reference source does not belong to the active tenant.",
                status_code=404,
            )
        if candidate.campaign_id != campaign.id:
            raise ReferenceLibraryError(
                "reference_asset_source_mismatch",
                "Reference candidate does not belong to this campaign.",
                status_code=404,
            )
        if candidate.status != "drive_ready":
            raise ReferenceLibraryError(
                "reference_asset_not_ready",
                "Only a durable ready candidate can enter the Reference Library.",
                status_code=409,
            )
        if not candidate.content_hash or not candidate.remote_file_id:
            raise ReferenceLibraryError(
                "reference_asset_storage_incomplete",
                "Ready candidate is missing durable storage identity.",
                status_code=409,
            )

        existing = self.repository.reference_asset_by_source(
            tenant_id,
            "pinterest",
            candidate.source_key,
        )
        if existing is None:
            existing = self.repository.reference_asset_by_content_hash(
                tenant_id,
                candidate.content_hash,
            )
        if existing is not None:
            return ReferenceAssetPromotion(asset=existing, created=False)

        context_profile = (
            campaign.product_context_json
            if isinstance(campaign.product_context_json, dict)
            else {}
        )
        themes = [
            str(value).strip()
            for value in context_profile.get("themes") or []
            if str(value or "").strip()
        ][:16]
        row = RrugcReferenceAssetModel(
            tenant_id=tenant_id,
            source_type="pinterest",
            source_key=candidate.source_key,
            source_url=candidate.pin_url,
            source_campaign_id=campaign.id,
            source_candidate_id=candidate.id,
            profile_key=REFERENCE_PROFILE_REALISTIC_PERSON_UGC,
            reference_type=_reference_type(candidate),
            status="ready",
            content_hash=candidate.content_hash,
            width=candidate.width,
            height=candidate.height,
            size_bytes=candidate.size_bytes,
            image_format=candidate.image_format,
            tags_json=[],
            themes_json=themes,
            quality_score=candidate.quality_score,
            visual_score=None,
            context_score=_context_score(candidate),
            usage_count=0,
            remote_file_id=candidate.remote_file_id,
            remote_folder_id=candidate.remote_folder_id,
            web_url=candidate.web_url,
            created_by_user_id=user_id,
        )
        try:
            self.repository.add_reference_asset(row)
            self.session.commit()
            self.session.refresh(row)
            return ReferenceAssetPromotion(asset=row, created=True)
        except IntegrityError:
            self.session.rollback()
            existing = self.repository.reference_asset_by_source(
                tenant_id,
                "pinterest",
                candidate.source_key,
            )
            if existing is None:
                existing = self.repository.reference_asset_by_content_hash(
                    tenant_id,
                    candidate.content_hash,
                )
            if existing is None:
                raise
            return ReferenceAssetPromotion(asset=existing, created=False)
