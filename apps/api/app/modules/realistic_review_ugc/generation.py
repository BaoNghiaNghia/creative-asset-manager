from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel,
    RrugcCandidateModel,
    RrugcGenerationAttemptModel,
    RrugcProductModel,
    RrugcProductReferenceModel,
)
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.realistic_review_ugc.service import RrugcError


DEFAULT_WORKER_SKILL_VERSION = "worker-hat-v1"
GENERATION_SOURCE_STATUSES = frozenset({"drive_ready", "rejected_duplicate"})
REQUIRED_HAT_REFERENCE_VIEWS = frozenset({"front"})


def product_snapshot(product: RrugcProductModel) -> dict:
    return {
        "id": product.id,
        "sku": product.sku,
        "name": product.name,
        "product_type": product.product_type,
        "color": product.color,
        "material": product.material,
        "crown_profile": product.crown_profile,
        "crown_height_mm": product.crown_height_mm,
        "brim_style": product.brim_style,
        "brim_length_mm": product.brim_length_mm,
        "circumference_mm": product.circumference_mm,
        "logo_position": product.logo_position,
        "fit_notes": product.fit_notes,
        "revision": product.revision,
    }


def latest_reference_snapshot(
    references: list[RrugcProductReferenceModel],
) -> list[dict]:
    latest: dict[str, RrugcProductReferenceModel] = {}
    for reference in references:
        if reference.status != "active":
            continue
        latest.setdefault(reference.view_type, reference)
    return [
        {
            "id": row.id,
            "view_type": row.view_type,
            "version": row.version,
            "content_hash": row.content_hash,
            "content_type": row.content_type,
            "width": row.width,
            "height": row.height,
            "remote_file_id": row.remote_file_id,
            "remote_folder_id": row.remote_folder_id,
            "web_url": row.web_url,
        }
        for _, row in sorted(latest.items())
    ]


def binding_is_generation_ready(reference_snapshot: list[dict] | None) -> bool:
    if not reference_snapshot:
        return False
    views = {
        str(item.get("view_type") or "")
        for item in reference_snapshot
        if item.get("remote_file_id")
    }
    return REQUIRED_HAT_REFERENCE_VIEWS.issubset(views)


def binding_fingerprint(
    *,
    product_snapshot_json: dict,
    reference_snapshot_json: list[dict],
) -> str:
    payload = json.dumps(
        {
            "product": product_snapshot_json,
            "references": reference_snapshot_json,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RrugcGenerationFoundation:
    """Durable product binding and immutable generation-attempt provenance.

    This phase deliberately stops at the prepared state. A later provider
    adapter can queue the existing Processing Job infrastructure without
    changing the campaign/candidate/product provenance captured here.
    """

    def __init__(self, session: Session):
        self.session = session
        self.repository = RrugcRepository(session)

    def bind_campaign_product(
        self,
        *,
        campaign: RrugcCampaignModel,
        product_id: str | None,
    ) -> RrugcCampaignModel:
        locked = self.repository.lock_campaign(campaign.tenant_id, campaign.id)
        if locked is None:
            raise RrugcError(
                "campaign_not_found", "Campaign not found.", status_code=404
            )

        if product_id is None:
            locked.product_id = None
            locked.product_revision = None
            locked.product_snapshot_json = None
            locked.product_reference_snapshot_json = None
            locked.product_bound_at = None
            self.session.commit()
            self.session.refresh(locked)
            return locked

        product = self.repository.get_product(campaign.tenant_id, product_id)
        if product is None:
            raise RrugcError("product_not_found", "Product not found.", status_code=404)
        if product.status != "active":
            raise RrugcError(
                "product_archived",
                "Archived products cannot be bound to a campaign.",
                status_code=409,
            )

        references = self.repository.list_product_references(
            campaign.tenant_id, product.id
        )
        locked.product_id = product.id
        locked.product_revision = product.revision
        locked.product_snapshot_json = product_snapshot(product)
        locked.product_reference_snapshot_json = latest_reference_snapshot(references)
        locked.product_bound_at = datetime.now(timezone.utc)
        self.session.commit()
        self.session.refresh(locked)
        return locked

    def binding_is_stale(self, campaign: RrugcCampaignModel) -> bool:
        if not campaign.product_id or not campaign.product_snapshot_json:
            return False
        product = self.repository.get_product(campaign.tenant_id, campaign.product_id)
        if product is None or product.status != "active":
            return True
        references = self.repository.list_product_references(
            campaign.tenant_id, product.id
        )
        current_product = product_snapshot(product)
        current_references = latest_reference_snapshot(references)
        return binding_fingerprint(
            product_snapshot_json=current_product,
            reference_snapshot_json=current_references,
        ) != binding_fingerprint(
            product_snapshot_json=dict(campaign.product_snapshot_json),
            reference_snapshot_json=list(campaign.product_reference_snapshot_json or []),
        )

    def prepare_attempt(
        self,
        *,
        campaign: RrugcCampaignModel,
        candidate: RrugcCandidateModel,
        user_id: str,
        generation_variant: int,
        worker_skill_version: str = DEFAULT_WORKER_SKILL_VERSION,
    ) -> tuple[RrugcGenerationAttemptModel, bool]:
        if candidate.status not in GENERATION_SOURCE_STATUSES:
            raise RrugcError(
                "generation_source_not_durable",
                "Save the approved person reference to Managed Drive before preparing generation.",
                status_code=409,
            )
        if not campaign.product_id or not campaign.product_snapshot_json:
            raise RrugcError(
                "campaign_product_required",
                "Bind a product SKU to the campaign before preparing generation.",
                status_code=409,
            )
        references = list(campaign.product_reference_snapshot_json or [])
        if self.binding_is_stale(campaign):
            raise RrugcError(
                "campaign_product_binding_stale",
                "Refresh the campaign product snapshot before preparing generation.",
                status_code=409,
            )
        if not binding_is_generation_ready(references):
            raise RrugcError(
                "product_reference_incomplete",
                "The bound product snapshot needs at least an active front reference in Managed Drive.",
                status_code=409,
            )

        product = self.repository.get_product(campaign.tenant_id, campaign.product_id)
        if product is None or product.status != "active":
            raise RrugcError(
                "product_unavailable",
                "The bound product is archived or unavailable.",
                status_code=409,
            )

        skill = worker_skill_version.strip()
        if not skill:
            raise RrugcError(
                "worker_skill_version_required",
                "Worker skill version is required.",
                status_code=422,
            )

        fingerprint = binding_fingerprint(
            product_snapshot_json=dict(campaign.product_snapshot_json),
            reference_snapshot_json=references,
        )
        raw_key = (
            f"{campaign.id}:{candidate.id}:{campaign.product_id}:"
            f"{fingerprint}:{generation_variant}:{skill}"
        )
        idempotency_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        existing = self.repository.generation_attempt_by_key(
            campaign.tenant_id, idempotency_key
        )
        if existing is not None:
            return existing, False

        row = RrugcGenerationAttemptModel(
            tenant_id=campaign.tenant_id,
            campaign_id=campaign.id,
            candidate_id=candidate.id,
            product_id=campaign.product_id,
            product_revision=int(campaign.product_revision or 1),
            product_snapshot_json=dict(campaign.product_snapshot_json),
            product_reference_snapshot_json=references,
            generation_variant=generation_variant,
            worker_skill_version=skill,
            status="prepared",
            idempotency_key=idempotency_key,
            created_by_user_id=user_id,
        )
        try:
            self.session.add(row)
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.repository.generation_attempt_by_key(
                campaign.tenant_id, idempotency_key
            )
            if existing is not None:
                return existing, False
            raise
        self.session.refresh(row)
        return row, True
