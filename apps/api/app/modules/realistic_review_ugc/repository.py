from __future__ import annotations

from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel,
    RrugcCandidateModel,
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
