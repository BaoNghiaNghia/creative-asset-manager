from __future__ import annotations

from collections import Counter

from sqlalchemy import case, delete, exists, func, or_, select, true
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel,
    RrugcCandidateModel,
    RrugcVisualFingerprintModel,
    RrugcAiFeedbackModel,
    RrugcGenerationAttemptModel,
    RrugcStage2JobModel,
    RrugcSupervisorResultModel,
    RrugcReviewTaskModel,
    RrugcExportModel,
    RrugcScoutAgentModel,
    RrugcScoutRunModel,
    RrugcSourcePlanModel,
    RrugcProductModel,
    RrugcProductVariantModel,
    RrugcProductReferenceModel,
    RrugcReferenceAssetModel,
    RrugcReferenceSetModel,
    RrugcReferenceSetItemModel,
    RrugcReferenceSeedModel,
)


class RrugcRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_campaigns(self, tenant_id: str, limit: int = 50) -> list[RrugcCampaignModel]:
        return list(self.session.scalars(
            select(RrugcCampaignModel)
            .where(
                RrugcCampaignModel.tenant_id == tenant_id,
                RrugcCampaignModel.status != "archived",
            )
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

    def campaign_usable_counts(self, tenant_id: str, campaign_id: str) -> Counter:
        manual_label = (
            RrugcCandidateModel.ai_signal_json["reference_manual_label"].as_string()
        )
        rows = self.session.execute(
            select(RrugcCandidateModel.status, func.count())
            .where(
                RrugcCandidateModel.tenant_id == tenant_id,
                RrugcCandidateModel.campaign_id == campaign_id,
                or_(
                    RrugcCandidateModel.ai_signal_json.is_(None),
                    manual_label.is_(None),
                    manual_label.notin_(("bad", "ai")),
                ),
            )
            .group_by(RrugcCandidateModel.status)
        ).all()
        return Counter({str(status): int(count) for status, count in rows})

    def campaign_usable_counts_many(
        self,
        tenant_id: str,
        campaign_ids: list[str],
    ) -> dict[str, Counter]:
        if not campaign_ids:
            return {}
        manual_label = (
            RrugcCandidateModel.ai_signal_json["reference_manual_label"].as_string()
        )
        rows = self.session.execute(
            select(
                RrugcCandidateModel.campaign_id,
                RrugcCandidateModel.status,
                func.count(),
            )
            .where(
                RrugcCandidateModel.tenant_id == tenant_id,
                RrugcCandidateModel.campaign_id.in_(campaign_ids),
                or_(
                    RrugcCandidateModel.ai_signal_json.is_(None),
                    manual_label.is_(None),
                    manual_label.notin_(("bad", "ai")),
                ),
            )
            .group_by(
                RrugcCandidateModel.campaign_id,
                RrugcCandidateModel.status,
            )
        ).all()
        result: dict[str, Counter] = {
            campaign_id: Counter() for campaign_id in campaign_ids
        }
        for campaign_id, status, count in rows:
            result[str(campaign_id)][str(status)] = int(count)
        return result

    def source_plan_status_counts(
        self,
        tenant_id: str,
        campaign_ids: list[str],
    ) -> dict[str, Counter]:
        if not campaign_ids:
            return {}
        rows = self.session.execute(
            select(
                RrugcSourcePlanModel.campaign_id,
                RrugcSourcePlanModel.status,
                func.count(),
            )
            .where(
                RrugcSourcePlanModel.tenant_id == tenant_id,
                RrugcSourcePlanModel.campaign_id.in_(campaign_ids),
            )
            .group_by(
                RrugcSourcePlanModel.campaign_id,
                RrugcSourcePlanModel.status,
            )
        ).all()
        result: dict[str, Counter] = {
            campaign_id: Counter() for campaign_id in campaign_ids
        }
        for campaign_id, status, count in rows:
            if campaign_id is None:
                continue
            result[str(campaign_id)][str(status)] = int(count)
        return result

    def recent_scout_shadow_stats(
        self,
        tenant_id: str,
        campaign_ids: list[str],
        *,
        per_campaign_limit: int = 50,
    ) -> dict[str, list[dict]]:
        if not campaign_ids:
            return {}
        row_number = func.row_number().over(
            partition_by=RrugcScoutRunModel.campaign_id,
            order_by=(
                RrugcScoutRunModel.created_at.desc(),
                RrugcScoutRunModel.id.desc(),
            ),
        ).label("row_number")
        ranked = (
            select(
                RrugcScoutRunModel.campaign_id.label("campaign_id"),
                RrugcScoutRunModel.keyword_stats_json.label("keyword_stats_json"),
                row_number,
            )
            .where(
                RrugcScoutRunModel.tenant_id == tenant_id,
                RrugcScoutRunModel.campaign_id.in_(campaign_ids),
                RrugcScoutRunModel.keyword_stats_json.is_not(None),
            )
            .subquery()
        )
        rows = self.session.execute(
            select(
                ranked.c.campaign_id,
                ranked.c.keyword_stats_json,
            )
            .where(ranked.c.row_number <= max(1, int(per_campaign_limit)))
        ).all()
        result: dict[str, list[dict]] = {
            campaign_id: [] for campaign_id in campaign_ids
        }
        for campaign_id, stats in rows:
            if not isinstance(stats, dict):
                continue
            shadow = stats.get("_jev_shadow")
            if isinstance(shadow, dict):
                result[str(campaign_id)].append(dict(shadow))
        return result

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

    def context_reanalysis_candidates(
        self,
        tenant_id: str,
        campaign_id: str,
        *,
        limit: int = 72,
    ) -> list[RrugcCandidateModel]:
        return list(self.session.scalars(
            select(RrugcCandidateModel)
            .where(
                RrugcCandidateModel.tenant_id == tenant_id,
                RrugcCandidateModel.campaign_id == campaign_id,
                RrugcCandidateModel.status.in_(
                    ("approved", "needs_review", "rejected_context")
                ),
            )
            .order_by(
                RrugcCandidateModel.created_at.desc(),
                RrugcCandidateModel.id.desc(),
            )
            .limit(max(1, int(limit)))
        ))

    def visual_fingerprint_rows(
        self, tenant_id: str, exclude_id: str, *, campaign_id: str | None = None
    ) -> list[str]:
        filters = [
            RrugcVisualFingerprintModel.tenant_id == tenant_id,
            RrugcVisualFingerprintModel.candidate_id != exclude_id,
            RrugcCandidateModel.status.in_(
                ("approved", "import_queued", "importing", "drive_ready")
            ),
        ]
        if campaign_id is not None:
            filters.append(RrugcVisualFingerprintModel.campaign_id == campaign_id)
        return list(self.session.scalars(
            select(RrugcVisualFingerprintModel.fingerprint)
            .join(
                RrugcCandidateModel,
                (
                    RrugcCandidateModel.tenant_id
                    == RrugcVisualFingerprintModel.tenant_id
                )
                & (
                    RrugcCandidateModel.id
                    == RrugcVisualFingerprintModel.candidate_id
                ),
            )
            .where(*filters)
        ))

    def replace_visual_fingerprints(
        self,
        candidate: RrugcCandidateModel,
        fingerprints: list[str],
    ) -> None:
        self.session.execute(
            delete(RrugcVisualFingerprintModel).where(
                RrugcVisualFingerprintModel.tenant_id == candidate.tenant_id,
                RrugcVisualFingerprintModel.candidate_id == candidate.id,
            )
        )
        eligible = candidate.status in {
            "approved",
            "import_queued",
            "importing",
            "drive_ready",
        }
        clean = (
            list(dict.fromkeys(
                value.strip()
                for value in fingerprints
                if isinstance(value, str) and value.strip()
            ))
            if eligible
            else []
        )
        self.session.add_all([
            RrugcVisualFingerprintModel(
                tenant_id=candidate.tenant_id,
                campaign_id=candidate.campaign_id,
                candidate_id=candidate.id,
                fingerprint=value,
            )
            for value in clean
        ])

    def campaign_diversity_signature_count(
        self,
        tenant_id: str,
        campaign_id: str,
        exclude_id: str,
        diversity_signature: str,
    ) -> int:
        return int(self.session.scalar(
            select(func.count())
            .select_from(RrugcCandidateModel)
            .where(
                RrugcCandidateModel.tenant_id == tenant_id,
                RrugcCandidateModel.campaign_id == campaign_id,
                RrugcCandidateModel.id != exclude_id,
                RrugcCandidateModel.status.in_(
                    ("approved", "import_queued", "importing", "drive_ready")
                ),
                RrugcCandidateModel.diversity_signature == diversity_signature,
            )
        ) or 0)

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

    def add_reference_asset(
        self,
        row: RrugcReferenceAssetModel,
    ) -> RrugcReferenceAssetModel:
        self.session.add(row)
        self.session.flush()
        return row

    def get_reference_asset(
        self,
        tenant_id: str,
        reference_asset_id: str,
    ) -> RrugcReferenceAssetModel | None:
        return self.session.scalar(
            select(RrugcReferenceAssetModel).where(
                RrugcReferenceAssetModel.tenant_id == tenant_id,
                RrugcReferenceAssetModel.id == reference_asset_id,
            )
        )

    def add_reference_set(
        self,
        row: RrugcReferenceSetModel,
    ) -> RrugcReferenceSetModel:
        self.session.add(row)
        self.session.flush()
        return row

    def get_reference_set(
        self,
        tenant_id: str,
        reference_set_id: str,
    ) -> RrugcReferenceSetModel | None:
        return self.session.scalar(
            select(RrugcReferenceSetModel).where(
                RrugcReferenceSetModel.tenant_id == tenant_id,
                RrugcReferenceSetModel.id == reference_set_id,
            )
        )

    def list_reference_sets(
        self,
        tenant_id: str,
        *,
        campaign_id: str | None = None,
        include_global: bool = False,
        status: str | None = "active",
        limit: int = 100,
        offset: int = 0,
    ) -> list[RrugcReferenceSetModel]:
        statement = select(RrugcReferenceSetModel).where(
            RrugcReferenceSetModel.tenant_id == tenant_id
        )
        if campaign_id:
            if include_global:
                statement = statement.where(
                    or_(
                        RrugcReferenceSetModel.campaign_id == campaign_id,
                        RrugcReferenceSetModel.campaign_id.is_(None),
                    )
                )
            else:
                statement = statement.where(
                    RrugcReferenceSetModel.campaign_id == campaign_id
                )
        if status:
            statement = statement.where(RrugcReferenceSetModel.status == status)
        return list(
            self.session.scalars(
                statement.order_by(
                    RrugcReferenceSetModel.updated_at.desc(),
                    RrugcReferenceSetModel.id.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
        )

    def add_reference_set_item(
        self,
        row: RrugcReferenceSetItemModel,
    ) -> RrugcReferenceSetItemModel:
        self.session.add(row)
        self.session.flush()
        return row

    def get_reference_set_item(
        self,
        tenant_id: str,
        reference_set_id: str,
        item_id: str,
    ) -> RrugcReferenceSetItemModel | None:
        return self.session.scalar(
            select(RrugcReferenceSetItemModel).where(
                RrugcReferenceSetItemModel.tenant_id == tenant_id,
                RrugcReferenceSetItemModel.reference_set_id == reference_set_id,
                RrugcReferenceSetItemModel.id == item_id,
            )
        )

    def reference_set_item_by_binding(
        self,
        tenant_id: str,
        reference_set_id: str,
        role: str,
        reference_asset_id: str,
    ) -> RrugcReferenceSetItemModel | None:
        return self.session.scalar(
            select(RrugcReferenceSetItemModel).where(
                RrugcReferenceSetItemModel.tenant_id == tenant_id,
                RrugcReferenceSetItemModel.reference_set_id == reference_set_id,
                RrugcReferenceSetItemModel.role == role,
                RrugcReferenceSetItemModel.reference_asset_id
                == reference_asset_id,
            )
        )

    def list_reference_set_items(
        self,
        tenant_id: str,
        reference_set_id: str,
    ) -> list[RrugcReferenceSetItemModel]:
        return list(
            self.session.scalars(
                select(RrugcReferenceSetItemModel)
                .where(
                    RrugcReferenceSetItemModel.tenant_id == tenant_id,
                    RrugcReferenceSetItemModel.reference_set_id
                    == reference_set_id,
                )
                .order_by(
                    RrugcReferenceSetItemModel.position.asc(),
                    RrugcReferenceSetItemModel.created_at.asc(),
                    RrugcReferenceSetItemModel.id.asc(),
                )
            )
        )

    def delete_reference_set_item(
        self,
        row: RrugcReferenceSetItemModel,
    ) -> None:
        self.session.delete(row)
        self.session.flush()

    def get_reference_seed(
        self,
        tenant_id: str,
        campaign_id: str,
        profile_key: str,
        reference_asset_id: str,
    ) -> RrugcReferenceSeedModel | None:
        return self.session.scalar(
            select(RrugcReferenceSeedModel).where(
                RrugcReferenceSeedModel.tenant_id == tenant_id,
                RrugcReferenceSeedModel.campaign_id == campaign_id,
                RrugcReferenceSeedModel.profile_key == profile_key,
                RrugcReferenceSeedModel.reference_asset_id == reference_asset_id,
            )
        )

    def list_reference_seeds(
        self,
        tenant_id: str,
        campaign_id: str,
        *,
        profile_key: str,
        label: str | None = None,
    ) -> list[RrugcReferenceSeedModel]:
        statement = select(RrugcReferenceSeedModel).where(
            RrugcReferenceSeedModel.tenant_id == tenant_id,
            RrugcReferenceSeedModel.campaign_id == campaign_id,
            RrugcReferenceSeedModel.profile_key == profile_key,
        )
        if label:
            statement = statement.where(RrugcReferenceSeedModel.label == label)
        return list(
            self.session.scalars(
                statement.order_by(
                    RrugcReferenceSeedModel.updated_at.desc(),
                    RrugcReferenceSeedModel.id.desc(),
                )
            )
        )

    def add_reference_seed(
        self,
        row: RrugcReferenceSeedModel,
    ) -> RrugcReferenceSeedModel:
        self.session.add(row)
        self.session.flush()
        return row

    def delete_reference_seed(self, row: RrugcReferenceSeedModel) -> None:
        self.session.delete(row)
        self.session.flush()

    def reference_asset_by_source(
        self,
        tenant_id: str,
        source_type: str,
        source_key: str,
    ) -> RrugcReferenceAssetModel | None:
        return self.session.scalar(
            select(RrugcReferenceAssetModel).where(
                RrugcReferenceAssetModel.tenant_id == tenant_id,
                RrugcReferenceAssetModel.source_type == source_type,
                RrugcReferenceAssetModel.source_key == source_key,
            )
        )

    def reference_asset_by_content_hash(
        self,
        tenant_id: str,
        content_hash: str,
    ) -> RrugcReferenceAssetModel | None:
        return self.session.scalar(
            select(RrugcReferenceAssetModel).where(
                RrugcReferenceAssetModel.tenant_id == tenant_id,
                RrugcReferenceAssetModel.content_hash == content_hash,
            )
        )

    def list_reference_assets(
        self,
        tenant_id: str,
        *,
        source_type: str | None = None,
        status: str | None = "ready",
        campaign_id: str | None = None,
        profile_key: str | None = None,
        reference_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RrugcReferenceAssetModel]:
        statement = select(RrugcReferenceAssetModel).where(
            RrugcReferenceAssetModel.tenant_id == tenant_id
        )
        if source_type:
            statement = statement.where(
                RrugcReferenceAssetModel.source_type == source_type
            )
        if status:
            statement = statement.where(RrugcReferenceAssetModel.status == status)
        if campaign_id:
            statement = statement.where(
                RrugcReferenceAssetModel.source_campaign_id == campaign_id
            )
        if profile_key:
            statement = statement.where(
                RrugcReferenceAssetModel.profile_key == profile_key
            )
        if reference_type:
            statement = statement.where(
                RrugcReferenceAssetModel.reference_type == reference_type
            )
        return list(
            self.session.scalars(
                statement.order_by(
                    RrugcReferenceAssetModel.updated_at.desc(),
                    RrugcReferenceAssetModel.id.desc(),
                )
                .limit(limit)
                .offset(offset)
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

    def analyzed_candidate_by_source_key_tenant(
        self,
        tenant_id: str,
        source_key: str,
        *,
        exclude_campaign_id: str | None = None,
    ) -> RrugcCandidateModel | None:
        statement = select(RrugcCandidateModel).where(
            RrugcCandidateModel.tenant_id == tenant_id,
            RrugcCandidateModel.source_key == source_key,
            RrugcCandidateModel.analyzed_at.is_not(None),
        )
        if exclude_campaign_id is not None:
            statement = statement.where(
                RrugcCandidateModel.campaign_id != exclude_campaign_id
            )
        return self.session.scalar(
            statement.order_by(
                RrugcCandidateModel.analyzed_at.desc(),
                RrugcCandidateModel.created_at.desc(),
            ).limit(1)
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

    def add_ai_feedback(
        self,
        row: RrugcAiFeedbackModel,
    ) -> RrugcAiFeedbackModel:
        self.session.add(row)
        self.session.flush()
        return row

    def ai_feedback_training_rows(
        self,
        tenant_id: str,
        *,
        limit: int = 500,
    ) -> list[tuple[str, float | None]]:
        rows = list(self.session.scalars(
            select(RrugcAiFeedbackModel)
            .where(
                RrugcAiFeedbackModel.tenant_id == tenant_id,
                RrugcAiFeedbackModel.label.in_(("real", "ai")),
            )
            .order_by(
                RrugcAiFeedbackModel.created_at.desc(),
                RrugcAiFeedbackModel.id.desc(),
            )
            .limit(limit)
        ))
        latest_by_candidate: dict[str, RrugcAiFeedbackModel] = {}
        for row in rows:
            if row.candidate_id not in latest_by_candidate:
                latest_by_candidate[row.candidate_id] = row
        return [
            (row.label, row.ai_risk_raw_score)
            for row in latest_by_candidate.values()
        ]

    def reference_feedback_training_rows(
        self,
        tenant_id: str,
        *,
        intent: str | None = None,
        legacy_campaign_id: str | None = None,
        profile_key: str | None = None,
        limit: int = 1000,
    ) -> list[tuple[str, dict | None]]:
        rows = list(self.session.scalars(
            select(RrugcAiFeedbackModel)
            .where(
                RrugcAiFeedbackModel.tenant_id == tenant_id,
                RrugcAiFeedbackModel.label.in_(("ref_good", "ref_bad", "ref_clear")),
            )
            .order_by(
                RrugcAiFeedbackModel.created_at.desc(),
                RrugcAiFeedbackModel.id.desc(),
            )
            .limit(limit)
        ))
        latest_by_scope: dict[tuple[str, str], RrugcAiFeedbackModel] = {}
        for row in rows:
            payload = row.signal_json if isinstance(row.signal_json, dict) else None
            row_profile = (
                str(payload.get("reference_profile_key") or "").strip()
                if payload
                else ""
            ) or "realistic-person-ugc"
            scope_key = (row.candidate_id, row_profile)
            if scope_key not in latest_by_scope:
                latest_by_scope[scope_key] = row

        result: list[tuple[str, dict | None]] = []
        for row in latest_by_scope.values():
            payload = row.signal_json if isinstance(row.signal_json, dict) else None
            row_profile = (
                str(payload.get("reference_profile_key") or "").strip()
                if payload
                else ""
            ) or "realistic-person-ugc"
            if profile_key is not None and row_profile != profile_key:
                continue
            if row.label not in {"ref_good", "ref_bad"}:
                continue
            if intent is not None:
                row_intent = payload.get("learning_intent") if payload else None
                same_intent = row_intent == intent
                same_legacy_campaign = (
                    row_intent is None
                    and legacy_campaign_id is not None
                    and row.campaign_id == legacy_campaign_id
                )
                if not same_intent and not same_legacy_campaign:
                    continue
            result.append((row.label, payload))
        return result


    def list_scout_agents(
        self,
        tenant_id: str,
        *,
        include_archived: bool = False,
        limit: int = 50,
    ) -> list[RrugcScoutAgentModel]:
        statement = select(RrugcScoutAgentModel).where(
            RrugcScoutAgentModel.tenant_id == tenant_id
        )
        if not include_archived:
            statement = statement.where(RrugcScoutAgentModel.active.is_(True))
        return list(
            self.session.scalars(
                statement.order_by(
                    RrugcScoutAgentModel.active.desc(),
                    RrugcScoutAgentModel.last_seen_at.desc().nullslast(),
                    RrugcScoutAgentModel.created_at.desc(),
                ).limit(limit)
            )
        )

    def lock_active_scout_agents(
        self,
        tenant_id: str,
    ) -> list[RrugcScoutAgentModel]:
        return list(
            self.session.scalars(
                select(RrugcScoutAgentModel)
                .where(
                    RrugcScoutAgentModel.tenant_id == tenant_id,
                    RrugcScoutAgentModel.active.is_(True),
                )
                .order_by(
                    RrugcScoutAgentModel.last_seen_at.desc().nullslast(),
                    RrugcScoutAgentModel.created_at.desc(),
                    RrugcScoutAgentModel.id.desc(),
                )
                .with_for_update()
            )
        )

    def get_scout_agent(
        self,
        tenant_id: str,
        agent_id: str,
    ) -> RrugcScoutAgentModel | None:
        return self.session.scalar(
            select(RrugcScoutAgentModel).where(
                RrugcScoutAgentModel.tenant_id == tenant_id,
                RrugcScoutAgentModel.id == agent_id,
            )
        )

    def get_scout_agent_unscoped(
        self,
        agent_id: str,
    ) -> RrugcScoutAgentModel | None:
        return self.session.get(RrugcScoutAgentModel, agent_id)

    def lock_scout_agent_unscoped(
        self,
        agent_id: str,
    ) -> RrugcScoutAgentModel | None:
        return self.session.scalar(
            select(RrugcScoutAgentModel)
            .where(RrugcScoutAgentModel.id == agent_id)
            .with_for_update()
        )

    def add_scout_agent(
        self,
        row: RrugcScoutAgentModel,
    ) -> RrugcScoutAgentModel:
        self.session.add(row)
        self.session.flush()
        return row

    def get_scout_run(
        self,
        tenant_id: str,
        run_id: str,
    ) -> RrugcScoutRunModel | None:
        return self.session.scalar(
            select(RrugcScoutRunModel).where(
                RrugcScoutRunModel.tenant_id == tenant_id,
                RrugcScoutRunModel.id == run_id,
            )
        )

    def lock_scout_run(
        self,
        tenant_id: str,
        run_id: str,
    ) -> RrugcScoutRunModel | None:
        return self.session.scalar(
            select(RrugcScoutRunModel)
            .where(
                RrugcScoutRunModel.tenant_id == tenant_id,
                RrugcScoutRunModel.id == run_id,
            )
            .with_for_update()
        )

    def list_scout_runs(
        self,
        tenant_id: str,
        *,
        campaign_id: str | None = None,
        agent_id: str | None = None,
        limit: int = 50,
    ) -> list[RrugcScoutRunModel]:
        statement = select(RrugcScoutRunModel).where(
            RrugcScoutRunModel.tenant_id == tenant_id
        )
        if campaign_id is not None:
            statement = statement.where(
                RrugcScoutRunModel.campaign_id == campaign_id
            )
        if agent_id is not None:
            statement = statement.where(
                RrugcScoutRunModel.agent_id == agent_id
            )
        return list(
            self.session.scalars(
                statement.order_by(
                    RrugcScoutRunModel.created_at.desc(),
                    RrugcScoutRunModel.id.desc(),
                ).limit(limit)
            )
        )

    def candidate_keyword_outcomes(
        self,
        tenant_id: str,
        campaign_id: str,
        *,
        limit: int = 2000,
    ) -> list[tuple[str, str, str | None, str | None]]:
        rows = self.session.execute(
            select(
                RrugcCandidateModel.status,
                RrugcCandidateModel.ai_signal_json,
            )
            .where(
                RrugcCandidateModel.tenant_id == tenant_id,
                RrugcCandidateModel.campaign_id == campaign_id,
                RrugcCandidateModel.ai_signal_json.is_not(None),
            )
            .order_by(
                RrugcCandidateModel.created_at.desc(),
                RrugcCandidateModel.id.desc(),
            )
            .limit(max(1, int(limit)))
        ).all()
        outcomes: list[tuple[str, str, str | None, str | None]] = []
        for status, signal in rows:
            query = signal.get("scout_query") if isinstance(signal, dict) else None
            reference_label = (
                signal.get("reference_manual_label")
                if isinstance(signal, dict)
                else None
            )
            context_label = (
                signal.get("context_manual_label")
                if isinstance(signal, dict)
                else None
            )
            if isinstance(query, str) and query.strip():
                outcomes.append((
                    query.strip(),
                    status,
                    reference_label if reference_label in {"good", "bad"} else None,
                    context_label if context_label in {"good", "wrong"} else None,
                ))
        return outcomes

    def claimable_campaigns(
        self,
        tenant_id: str,
        *,
        now,
        limit: int = 20,
        source_plan_only: bool = False,
    ) -> list[RrugcCampaignModel]:
        source_scope = (
            exists(
                select(RrugcSourcePlanModel.id).where(
                    RrugcSourcePlanModel.tenant_id == tenant_id,
                    RrugcSourcePlanModel.campaign_id == RrugcCampaignModel.id,
                    RrugcSourcePlanModel.status == "ready",
                )
            )
            if source_plan_only
            else true()
        )
        manual_label = (
            RrugcCandidateModel.ai_signal_json["reference_manual_label"].as_string()
        )
        usable_candidate = or_(
            RrugcCandidateModel.ai_signal_json.is_(None),
            manual_label.is_(None),
            manual_label.notin_(("bad", "ai")),
        )

        def usable_count_for(statuses: tuple[str, ...]):
            return (
                select(func.count(RrugcCandidateModel.id))
                .where(
                    RrugcCandidateModel.tenant_id == tenant_id,
                    RrugcCandidateModel.campaign_id == RrugcCampaignModel.id,
                    RrugcCandidateModel.status.in_(statuses),
                    usable_candidate,
                )
                .correlate(RrugcCampaignModel)
                .scalar_subquery()
            )

        # Scarcity-first scheduling prevents a small set of campaigns from
        # repeatedly consuming Scout runs while other source groups still have
        # zero references. Pipeline count is the primary signal because rows
        # already waiting for AI/import are real in-flight capacity and should
        # not trigger more Pinterest work until sparser groups are served.
        auto_import_pipeline = usable_count_for(
            (
                "analysis_queued",
                "analyzing",
                "approved",
                "import_queued",
                "importing",
                "drive_ready",
            )
        )
        manual_pipeline = usable_count_for(
            ("analysis_queued", "analyzing", "approved")
        )
        drive_ready_count = usable_count_for(("drive_ready",))
        approved_count = usable_count_for(("approved",))
        pipeline_count = case(
            (RrugcCampaignModel.auto_import.is_(True), auto_import_pipeline),
            else_=manual_pipeline,
        )
        progress_count = case(
            (RrugcCampaignModel.auto_import.is_(True), drive_ready_count),
            else_=approved_count,
        )

        return list(
            self.session.scalars(
                select(RrugcCampaignModel)
                .where(
                    RrugcCampaignModel.tenant_id == tenant_id,
                    RrugcCampaignModel.status == "running",
                    RrugcCampaignModel.auto_scout.is_(True),
                    source_scope,
                    or_(
                        RrugcCampaignModel.scan_next_at.is_(None),
                        RrugcCampaignModel.scan_next_at <= now,
                    ),
                    or_(
                        RrugcCampaignModel.scan_lease_expires_at.is_(None),
                        RrugcCampaignModel.scan_lease_expires_at <= now,
                    ),
                )
                .order_by(
                    pipeline_count.asc(),
                    progress_count.asc(),
                    RrugcCampaignModel.scan_last_started_at.asc().nullsfirst(),
                    RrugcCampaignModel.scan_next_at.asc().nullsfirst(),
                    RrugcCampaignModel.updated_at.asc(),
                    RrugcCampaignModel.id.asc(),
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )

    def candidate_by_pin_url(
        self,
        tenant_id: str,
        campaign_id: str,
        pin_url: str,
    ) -> RrugcCandidateModel | None:
        return self.session.scalar(
            select(RrugcCandidateModel).where(
                RrugcCandidateModel.tenant_id == tenant_id,
                RrugcCandidateModel.campaign_id == campaign_id,
                RrugcCandidateModel.pin_url == pin_url,
            )
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

    def product_by_source_url(
        self,
        tenant_id: str,
        source_url: str,
    ) -> RrugcProductModel | None:
        return self.session.scalar(
            select(RrugcProductModel).where(
                RrugcProductModel.tenant_id == tenant_id,
                RrugcProductModel.source_url == source_url,
            )
        )

    def list_product_variants(
        self,
        tenant_id: str,
        product_id: str,
        *,
        include_archived: bool = False,
    ) -> list[RrugcProductVariantModel]:
        statement = select(RrugcProductVariantModel).where(
            RrugcProductVariantModel.tenant_id == tenant_id,
            RrugcProductVariantModel.product_id == product_id,
        )
        if not include_archived:
            statement = statement.where(RrugcProductVariantModel.status == "active")
        return list(self.session.scalars(
            statement.order_by(
                RrugcProductVariantModel.position.asc(),
                RrugcProductVariantModel.created_at.asc(),
            )
        ))

    def get_product_variant(
        self,
        tenant_id: str,
        product_id: str,
        variant_id: str,
    ) -> RrugcProductVariantModel | None:
        return self.session.scalar(
            select(RrugcProductVariantModel).where(
                RrugcProductVariantModel.tenant_id == tenant_id,
                RrugcProductVariantModel.product_id == product_id,
                RrugcProductVariantModel.id == variant_id,
            )
        )

    def product_variant_by_source_id(
        self,
        tenant_id: str,
        product_id: str,
        source_variant_id: str,
    ) -> RrugcProductVariantModel | None:
        return self.session.scalar(
            select(RrugcProductVariantModel).where(
                RrugcProductVariantModel.tenant_id == tenant_id,
                RrugcProductVariantModel.product_id == product_id,
                RrugcProductVariantModel.source_variant_id == source_variant_id,
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
        *,
        variant_id: str | None = None,
    ) -> RrugcProductReferenceModel | None:
        variant_clause = (
            RrugcProductReferenceModel.variant_id.is_(None)
            if variant_id is None
            else RrugcProductReferenceModel.variant_id == variant_id
        )
        return self.session.scalar(
            select(RrugcProductReferenceModel)
            .where(
                RrugcProductReferenceModel.tenant_id == tenant_id,
                RrugcProductReferenceModel.product_id == product_id,
                RrugcProductReferenceModel.view_type == view_type,
                RrugcProductReferenceModel.content_hash == content_hash,
                RrugcProductReferenceModel.status == "active",
                variant_clause,
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

    def stage2_job_by_key(
        self,
        tenant_id: str,
        idempotency_key: str,
    ) -> RrugcStage2JobModel | None:
        return self.session.scalar(
            select(RrugcStage2JobModel).where(
                RrugcStage2JobModel.tenant_id == tenant_id,
                RrugcStage2JobModel.idempotency_key == idempotency_key,
            )
        )

    def list_stage2_jobs(
        self,
        tenant_id: str,
        *,
        source_plan_ids: list[str] | None = None,
        limit: int = 1000,
    ) -> list[RrugcStage2JobModel]:
        statement = select(RrugcStage2JobModel).where(
            RrugcStage2JobModel.tenant_id == tenant_id
        )
        if source_plan_ids:
            statement = statement.where(
                RrugcStage2JobModel.source_plan_id.in_(source_plan_ids)
            )
        return list(
            self.session.scalars(
                statement.order_by(
                    RrugcStage2JobModel.created_at.desc(),
                    RrugcStage2JobModel.id.desc(),
                ).limit(limit)
            )
        )

    def get_stage2_job(
        self,
        tenant_id: str,
        job_id: str,
    ) -> RrugcStage2JobModel | None:
        return self.session.scalar(
            select(RrugcStage2JobModel).where(
                RrugcStage2JobModel.tenant_id == tenant_id,
                RrugcStage2JobModel.id == job_id,
            )
        )

    def lock_stage2_job(
        self,
        tenant_id: str,
        job_id: str,
    ) -> RrugcStage2JobModel | None:
        return self.session.scalar(
            select(RrugcStage2JobModel)
            .where(
                RrugcStage2JobModel.tenant_id == tenant_id,
                RrugcStage2JobModel.id == job_id,
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

    def get_generation_attempt(
        self, tenant_id: str, attempt_id: str
    ) -> RrugcGenerationAttemptModel | None:
        return self.session.scalar(
            select(RrugcGenerationAttemptModel).where(
                RrugcGenerationAttemptModel.tenant_id == tenant_id,
                RrugcGenerationAttemptModel.id == attempt_id,
            )
        )

    def lock_generation_attempt(
        self, tenant_id: str, attempt_id: str
    ) -> RrugcGenerationAttemptModel | None:
        return self.session.scalar(
            select(RrugcGenerationAttemptModel)
            .where(
                RrugcGenerationAttemptModel.tenant_id == tenant_id,
                RrugcGenerationAttemptModel.id == attempt_id,
            )
            .with_for_update()
        )

    def list_supervisor_results(
        self,
        tenant_id: str,
        campaign_id: str,
        *,
        generation_attempt_id: str | None = None,
        limit: int = 200,
    ) -> list[RrugcSupervisorResultModel]:
        statement = select(RrugcSupervisorResultModel).where(
            RrugcSupervisorResultModel.tenant_id == tenant_id,
            RrugcSupervisorResultModel.campaign_id == campaign_id,
        )
        if generation_attempt_id is not None:
            statement = statement.where(
                RrugcSupervisorResultModel.generation_attempt_id == generation_attempt_id
            )
        return list(
            self.session.scalars(
                statement.order_by(
                    RrugcSupervisorResultModel.created_at.desc(),
                    RrugcSupervisorResultModel.id.desc(),
                ).limit(limit)
            )
        )

    def get_supervisor_result(
        self, tenant_id: str, result_id: str
    ) -> RrugcSupervisorResultModel | None:
        return self.session.scalar(
            select(RrugcSupervisorResultModel).where(
                RrugcSupervisorResultModel.tenant_id == tenant_id,
                RrugcSupervisorResultModel.id == result_id,
            )
        )

    def lock_supervisor_result(
        self, tenant_id: str, result_id: str
    ) -> RrugcSupervisorResultModel | None:
        return self.session.scalar(
            select(RrugcSupervisorResultModel)
            .where(
                RrugcSupervisorResultModel.tenant_id == tenant_id,
                RrugcSupervisorResultModel.id == result_id,
            )
            .with_for_update()
        )

    def supervisor_result_for_attempt(
        self,
        tenant_id: str,
        generation_attempt_id: str,
        supervisor_skill_version: str,
    ) -> RrugcSupervisorResultModel | None:
        return self.session.scalar(
            select(RrugcSupervisorResultModel).where(
                RrugcSupervisorResultModel.tenant_id == tenant_id,
                RrugcSupervisorResultModel.generation_attempt_id == generation_attempt_id,
                RrugcSupervisorResultModel.supervisor_skill_version == supervisor_skill_version,
            )
        )

    def correction_attempt_for_supervisor(
        self, tenant_id: str, supervisor_result_id: str
    ) -> RrugcGenerationAttemptModel | None:
        return self.session.scalar(
            select(RrugcGenerationAttemptModel).where(
                RrugcGenerationAttemptModel.tenant_id == tenant_id,
                RrugcGenerationAttemptModel.correction_supervisor_result_id == supervisor_result_id,
            )
        )

    def review_task_for_attempt(
        self, tenant_id: str, generation_attempt_id: str
    ) -> RrugcReviewTaskModel | None:
        return self.session.scalar(
            select(RrugcReviewTaskModel).where(
                RrugcReviewTaskModel.tenant_id == tenant_id,
                RrugcReviewTaskModel.generation_attempt_id == generation_attempt_id,
            )
        )

    def get_review_task(
        self, tenant_id: str, task_id: str
    ) -> RrugcReviewTaskModel | None:
        return self.session.scalar(
            select(RrugcReviewTaskModel).where(
                RrugcReviewTaskModel.tenant_id == tenant_id,
                RrugcReviewTaskModel.id == task_id,
            )
        )

    def lock_review_task(
        self, tenant_id: str, task_id: str
    ) -> RrugcReviewTaskModel | None:
        return self.session.scalar(
            select(RrugcReviewTaskModel)
            .where(
                RrugcReviewTaskModel.tenant_id == tenant_id,
                RrugcReviewTaskModel.id == task_id,
            )
            .with_for_update()
        )

    def list_review_tasks(
        self,
        tenant_id: str,
        *,
        status: str | None = None,
        priority: str | None = None,
        campaign_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[RrugcReviewTaskModel], int]:
        statement = select(RrugcReviewTaskModel).where(
            RrugcReviewTaskModel.tenant_id == tenant_id
        )
        if status is not None:
            statement = statement.where(RrugcReviewTaskModel.status == status)
        if priority is not None:
            statement = statement.where(RrugcReviewTaskModel.priority == priority)
        if campaign_id is not None:
            statement = statement.where(
                RrugcReviewTaskModel.campaign_id == campaign_id
            )
        total = int(
            self.session.scalar(
                select(func.count()).select_from(statement.order_by(None).subquery())
            )
            or 0
        )
        rows = list(
            self.session.scalars(
                statement.order_by(
                    RrugcReviewTaskModel.created_at.desc(),
                    RrugcReviewTaskModel.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        )
        return rows, total

    def terminal_supervisor_results_without_review(
        self, tenant_id: str, *, limit: int = 100
    ) -> list[RrugcSupervisorResultModel]:
        statement = (
            select(RrugcSupervisorResultModel)
            .outerjoin(
                RrugcReviewTaskModel,
                (RrugcReviewTaskModel.tenant_id == RrugcSupervisorResultModel.tenant_id)
                & (
                    RrugcReviewTaskModel.generation_attempt_id
                    == RrugcSupervisorResultModel.generation_attempt_id
                ),
            )
            .where(
                RrugcSupervisorResultModel.tenant_id == tenant_id,
                RrugcSupervisorResultModel.status.in_(
                    ("pass", "needs_human_review")
                ),
                RrugcReviewTaskModel.id.is_(None),
            )
            .order_by(
                RrugcSupervisorResultModel.completed_at.asc().nullsfirst(),
                RrugcSupervisorResultModel.id.asc(),
            )
            .limit(limit)
        )
        return list(self.session.scalars(statement))

    def export_for_attempt(
        self, tenant_id: str, generation_attempt_id: str
    ) -> RrugcExportModel | None:
        return self.session.scalar(
            select(RrugcExportModel).where(
                RrugcExportModel.tenant_id == tenant_id,
                RrugcExportModel.generation_attempt_id == generation_attempt_id,
            )
        )

    def get_export(
        self, tenant_id: str, export_id: str
    ) -> RrugcExportModel | None:
        return self.session.scalar(
            select(RrugcExportModel).where(
                RrugcExportModel.tenant_id == tenant_id,
                RrugcExportModel.id == export_id,
            )
        )

    def list_exports(
        self,
        tenant_id: str,
        *,
        campaign_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[RrugcExportModel], int]:
        statement = select(RrugcExportModel).where(
            RrugcExportModel.tenant_id == tenant_id
        )
        if campaign_id is not None:
            statement = statement.where(
                RrugcExportModel.campaign_id == campaign_id
            )
        total = int(
            self.session.scalar(
                select(func.count()).select_from(statement.order_by(None).subquery())
            )
            or 0
        )
        rows = list(
            self.session.scalars(
                statement.order_by(
                    RrugcExportModel.exported_at.desc(),
                    RrugcExportModel.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        )
        return rows, total

    def reviewed_generation_attempts(
        self,
        tenant_id: str,
        *,
        skill_name: str,
        limit: int = 500,
    ) -> list[RrugcGenerationAttemptModel]:
        return list(
            self.session.scalars(
                select(RrugcGenerationAttemptModel)
                .where(
                    RrugcGenerationAttemptModel.tenant_id == tenant_id,
                    RrugcGenerationAttemptModel.worker_skill_version == skill_name,
                    RrugcGenerationAttemptModel.status == "completed",
                    RrugcGenerationAttemptModel.review_status.in_(
                        ("approved", "rejected")
                    ),
                )
                .order_by(
                    RrugcGenerationAttemptModel.reviewed_at.desc().nullslast(),
                    RrugcGenerationAttemptModel.id.desc(),
                )
                .limit(limit)
            )
        )

    def exportable_attempts(
        self,
        tenant_id: str,
        *,
        campaign_id: str,
        limit: int = 100,
    ) -> list[RrugcGenerationAttemptModel]:
        return list(
            self.session.scalars(
                select(RrugcGenerationAttemptModel)
                .where(
                    RrugcGenerationAttemptModel.tenant_id == tenant_id,
                    RrugcGenerationAttemptModel.campaign_id == campaign_id,
                    RrugcGenerationAttemptModel.status == "completed",
                    RrugcGenerationAttemptModel.review_status == "approved",
                    RrugcGenerationAttemptModel.export_status == "export_ready",
                    RrugcGenerationAttemptModel.output_content_hash.is_not(None),
                    RrugcGenerationAttemptModel.output_remote_file_id.is_not(None),
                )
                .order_by(
                    RrugcGenerationAttemptModel.reviewed_at.asc().nullsfirst(),
                    RrugcGenerationAttemptModel.id.asc(),
                )
                .limit(limit)
            )
        )

    def campaign_export_summary(
        self, tenant_id: str, campaign_id: str
    ) -> dict[str, int]:
        base = (
            RrugcGenerationAttemptModel.tenant_id == tenant_id,
            RrugcGenerationAttemptModel.campaign_id == campaign_id,
        )

        def count(*extra) -> int:
            return int(
                self.session.scalar(
                    select(func.count())
                    .select_from(RrugcGenerationAttemptModel)
                    .where(*base, *extra)
                )
                or 0
            )

        return {
            "generated": count(RrugcGenerationAttemptModel.status == "completed"),
            "review_pending": count(RrugcGenerationAttemptModel.review_status == "pending"),
            "approved": count(RrugcGenerationAttemptModel.review_status == "approved"),
            "rejected": count(RrugcGenerationAttemptModel.review_status == "rejected"),
            "export_ready": count(RrugcGenerationAttemptModel.export_status == "export_ready"),
            "exported": count(RrugcGenerationAttemptModel.export_status == "exported"),
        }
