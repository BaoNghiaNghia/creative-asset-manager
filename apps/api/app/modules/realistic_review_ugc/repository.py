from __future__ import annotations

from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel,
    RrugcCandidateModel,
    RrugcGenerationAttemptModel,
    RrugcProductModel,
    RrugcProductReferenceModel,
)


class RrugcRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_campaigns(self, tenant_id: str, limit: int = 50) -> list[RrugcCampaignModel]:
        return list(self.session.scalars(
            select(RrugcCampaignModel)
            .where(RrugcCampaignModel.tenant_id == tenant_id)
            .order_by(RrugcCampaignModel.updated_at.desc(), RrugcCampaignModel.id.desc())
            .limit(limit)
        ))

    def get_campaign(self, tenant_id: str, campaign_id: str) -> RrugcCampaignModel | None:
        return self.session.scalar(
            select(RrugcCampaignModel).where(
                RrugcCampaignModel.tenant_id == tenant_id,
                RrugcCampaignModel.id == campaign_id,
            )
        )

    def get_campaign_unscoped(self, campaign_id: str) -> RrugcCampaignModel | None:
        return self.session.get(RrugcCampaignModel, campaign_id)

    def add_campaign(self, row: RrugcCampaignModel) -> RrugcCampaignModel:
        self.session.add(row)
        self.session.flush()
        return row

    def campaign_counts(self, tenant_id: str, campaign_id: str) -> Counter:
        rows = self.session.execute(
            select(RrugcCandidateModel.status, func.count())
            .where(
                RrugcCandidateModel.tenant_id == tenant_id,
                RrugcCandidateModel.campaign_id == campaign_id,
            )
            .group_by(RrugcCandidateModel.status)
        ).all()
        return Counter({str(status): int(count) for status, count in rows})

    def list_candidates(
        self, tenant_id: str, campaign_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[RrugcCandidateModel]:
        return list(self.session.scalars(
            select(RrugcCandidateModel)
            .where(
                RrugcCandidateModel.tenant_id == tenant_id,
                RrugcCandidateModel.campaign_id == campaign_id,
            )
            .order_by(RrugcCandidateModel.created_at.desc(), RrugcCandidateModel.id.desc())
            .limit(limit)
            .offset(offset)
        ))

    def get_candidate(
        self, tenant_id: str, campaign_id: str, candidate_id: str
    ) -> RrugcCandidateModel | None:
        return self.session.scalar(
            select(RrugcCandidateModel).where(
                RrugcCandidateModel.tenant_id == tenant_id,
                RrugcCandidateModel.campaign_id == campaign_id,
                RrugcCandidateModel.id == candidate_id,
            )
        )

    def candidate_by_source_key(
        self, tenant_id: str, campaign_id: str, source_key: str
    ) -> RrugcCandidateModel | None:
        return self.session.scalar(
            select(RrugcCandidateModel).where(
                RrugcCandidateModel.tenant_id == tenant_id,
                RrugcCandidateModel.campaign_id == campaign_id,
                RrugcCandidateModel.source_key == source_key,
            )
        )

    def drive_candidate_by_hash(
        self, tenant_id: str, content_hash: str, exclude_id: str
    ) -> RrugcCandidateModel | None:
        return self.session.scalar(
            select(RrugcCandidateModel)
            .where(
                RrugcCandidateModel.tenant_id == tenant_id,
                RrugcCandidateModel.content_hash == content_hash,
                RrugcCandidateModel.status == "drive_ready",
                RrugcCandidateModel.id != exclude_id,
            )
            .order_by(RrugcCandidateModel.imported_at.desc())
            .limit(1)
        )

    def list_products(
        self, tenant_id: str, *, include_archived: bool = False, limit: int = 200
    ) -> list[RrugcProductModel]:
        statement = select(RrugcProductModel).where(
            RrugcProductModel.tenant_id == tenant_id
        )
        if not include_archived:
            statement = statement.where(RrugcProductModel.status == "active")
        return list(self.session.scalars(
            statement
            .order_by(RrugcProductModel.updated_at.desc(), RrugcProductModel.id.desc())
            .limit(limit)
        ))

    def get_product(self, tenant_id: str, product_id: str) -> RrugcProductModel | None:
        return self.session.scalar(
            select(RrugcProductModel).where(
                RrugcProductModel.tenant_id == tenant_id,
                RrugcProductModel.id == product_id,
            )
        )

    def lock_product(self, tenant_id: str, product_id: str) -> RrugcProductModel | None:
        return self.session.scalar(
            select(RrugcProductModel)
            .where(
                RrugcProductModel.tenant_id == tenant_id,
                RrugcProductModel.id == product_id,
            )
            .with_for_update()
        )

    def product_by_sku(self, tenant_id: str, sku: str) -> RrugcProductModel | None:
        return self.session.scalar(
            select(RrugcProductModel).where(
                RrugcProductModel.tenant_id == tenant_id,
                RrugcProductModel.sku == sku,
            )
        )

    def list_product_references(
        self,
        tenant_id: str,
        product_id: str,
        *,
        include_archived: bool = False,
    ) -> list[RrugcProductReferenceModel]:
        statement = select(RrugcProductReferenceModel).where(
            RrugcProductReferenceModel.tenant_id == tenant_id,
            RrugcProductReferenceModel.product_id == product_id,
        )
        if not include_archived:
            statement = statement.where(RrugcProductReferenceModel.status == "active")
        return list(self.session.scalars(
            statement.order_by(
                RrugcProductReferenceModel.view_type.asc(),
                RrugcProductReferenceModel.version.desc(),
                RrugcProductReferenceModel.created_at.desc(),
            )
        ))

    def get_product_reference(
        self, tenant_id: str, product_id: str, reference_id: str
    ) -> RrugcProductReferenceModel | None:
        return self.session.scalar(
            select(RrugcProductReferenceModel).where(
                RrugcProductReferenceModel.tenant_id == tenant_id,
                RrugcProductReferenceModel.product_id == product_id,
                RrugcProductReferenceModel.id == reference_id,
            )
        )

    def latest_reference_version(
        self, tenant_id: str, product_id: str, view_type: str
    ) -> int:
        value = self.session.scalar(
            select(func.max(RrugcProductReferenceModel.version)).where(
                RrugcProductReferenceModel.tenant_id == tenant_id,
                RrugcProductReferenceModel.product_id == product_id,
                RrugcProductReferenceModel.view_type == view_type,
            )
        )
        return int(value or 0)

    def product_reference_by_hash(
        self,
        tenant_id: str,
        product_id: str,
        view_type: str,
        content_hash: str,
    ) -> RrugcProductReferenceModel | None:
        return self.session.scalar(
            select(RrugcProductReferenceModel)
            .where(
                RrugcProductReferenceModel.tenant_id == tenant_id,
                RrugcProductReferenceModel.product_id == product_id,
                RrugcProductReferenceModel.view_type == view_type,
                RrugcProductReferenceModel.content_hash == content_hash,
                RrugcProductReferenceModel.status == "active",
            )
            .order_by(RrugcProductReferenceModel.version.desc())
            .limit(1)
        )

    def reusable_product_reference_by_hash(
        self, tenant_id: str, content_hash: str
    ) -> RrugcProductReferenceModel | None:
        return self.session.scalar(
            select(RrugcProductReferenceModel)
            .where(
                RrugcProductReferenceModel.tenant_id == tenant_id,
                RrugcProductReferenceModel.content_hash == content_hash,
                RrugcProductReferenceModel.status == "active",
                RrugcProductReferenceModel.remote_file_id.is_not(None),
            )
            .order_by(RrugcProductReferenceModel.created_at.desc())
            .limit(1)
        )

    def lock_campaign(self, tenant_id: str, campaign_id: str) -> RrugcCampaignModel | None:
        return self.session.scalar(
            select(RrugcCampaignModel)
            .where(
                RrugcCampaignModel.tenant_id == tenant_id,
                RrugcCampaignModel.id == campaign_id,
            )
            .with_for_update()
        )

    def generation_attempt_by_key(
        self, tenant_id: str, idempotency_key: str
    ) -> RrugcGenerationAttemptModel | None:
        return self.session.scalar(
            select(RrugcGenerationAttemptModel).where(
                RrugcGenerationAttemptModel.tenant_id == tenant_id,
                RrugcGenerationAttemptModel.idempotency_key == idempotency_key,
            )
        )

    def list_generation_attempts(
        self,
        tenant_id: str,
        campaign_id: str,
        *,
        candidate_id: str | None = None,
        limit: int = 200,
    ) -> list[RrugcGenerationAttemptModel]:
        statement = select(RrugcGenerationAttemptModel).where(
            RrugcGenerationAttemptModel.tenant_id == tenant_id,
            RrugcGenerationAttemptModel.campaign_id == campaign_id,
        )
        if candidate_id is not None:
            statement = statement.where(
                RrugcGenerationAttemptModel.candidate_id == candidate_id
            )
        return list(self.session.scalars(
            statement
            .order_by(
                RrugcGenerationAttemptModel.created_at.desc(),
                RrugcGenerationAttemptModel.id.desc(),
            )
            .limit(limit)
        ))
