from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.assets.model import AssetModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.search.governance_model import ActiveAnalysisAuditModel, ActiveAssetAnalysisModel


class AnalysisActivationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ActivationResult:
    active: ActiveAssetAnalysisModel
    previous_analysis_id: str | None


class ActiveAnalysisService:
    """Owns the single deterministic search analysis pointer.

    The asset row is locked before reading/updating the pointer, which serializes
    first activation and replacement across worker processes.
    """

    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _is_valid(analysis: AssetAiAnalysisModel | None) -> bool:
        return bool(
            analysis is not None
            and analysis.status == "completed"
            and analysis.completed_at is not None
            and isinstance(analysis.metadata_json, dict)
            and isinstance(analysis.search_projection, dict)
            and analysis.search_projection_version
            and not analysis.validation_errors_json
        )

    def _lock_asset(self, tenant_id: str, asset_id: str) -> AssetModel:
        asset = self.session.scalar(
            select(AssetModel).where(
                AssetModel.tenant_id == tenant_id,
                AssetModel.id == asset_id,
            ).with_for_update()
        )
        if asset is None:
            raise LookupError(asset_id)
        return asset

    def get(self, tenant_id: str, asset_id: str, metadata_profile_id: str, search_context: str = "search_v2") -> ActiveAssetAnalysisModel | None:
        return self.session.scalar(
            select(ActiveAssetAnalysisModel).where(
                ActiveAssetAnalysisModel.tenant_id == tenant_id,
                ActiveAssetAnalysisModel.asset_id == asset_id,
                ActiveAssetAnalysisModel.metadata_profile_id == metadata_profile_id,
                ActiveAssetAnalysisModel.search_context == search_context,
            )
        )

    def resolve(self, tenant_id: str, asset_id: str, metadata_profile_id: str, search_context: str = "search_v2") -> AssetAiAnalysisModel:
        pointer = self.get(tenant_id, asset_id, metadata_profile_id, search_context)
        if pointer is None:
            raise AnalysisActivationError("no active analysis is selected")
        analysis = self.session.get(AssetAiAnalysisModel, pointer.analysis_id)
        if (
            analysis is None
            or analysis.tenant_id != tenant_id
            or analysis.asset_id != asset_id
            or analysis.metadata_profile_id != metadata_profile_id
            or not self._is_valid(analysis)
        ):
            raise AnalysisActivationError("active analysis reference is invalid")
        return analysis

    def activate(
        self,
        *,
        tenant_id: str,
        asset_id: str,
        analysis_id: str,
        actor_id: str,
        reason: str | None = None,
        search_context: str = "search_v2",
        action: str = "activate",
    ) -> ActivationResult:
        if action not in {"activate", "rollback"}:
            raise AnalysisActivationError("unsupported activation action")
        self._lock_asset(tenant_id, asset_id)
        analysis = self.session.scalar(
            select(AssetAiAnalysisModel).where(
                AssetAiAnalysisModel.id == analysis_id,
                AssetAiAnalysisModel.tenant_id == tenant_id,
                AssetAiAnalysisModel.asset_id == asset_id,
            )
        )
        if (
            not self._is_valid(analysis)
        ):
            raise AnalysisActivationError("only a completed, valid, projected analysis can be activated")
        pointer = self.session.scalar(
            select(ActiveAssetAnalysisModel).where(
                ActiveAssetAnalysisModel.tenant_id == tenant_id,
                ActiveAssetAnalysisModel.asset_id == asset_id,
                ActiveAssetAnalysisModel.metadata_profile_id == analysis.metadata_profile_id,
                ActiveAssetAnalysisModel.search_context == search_context,
            ).with_for_update()
        )
        previous = pointer.analysis_id if pointer else None
        if pointer is None:
            pointer = ActiveAssetAnalysisModel(
                tenant_id=tenant_id,
                asset_id=asset_id,
                metadata_profile_id=analysis.metadata_profile_id,
                search_context=search_context,
                analysis_id=analysis.id,
                activated_by=actor_id,
                activation_reason=reason,
            )
            self.session.add(pointer)
        else:
            pointer.analysis_id = analysis.id
            pointer.activated_by = actor_id
            pointer.activation_reason = reason
            pointer.updated_at = datetime.now(timezone.utc)
        self.session.add(ActiveAnalysisAuditModel(
            tenant_id=tenant_id,
            asset_id=asset_id,
            metadata_profile_id=analysis.metadata_profile_id,
            search_context=search_context,
            actor_id=actor_id,
            action=action,
            previous_analysis_id=previous,
            analysis_id=analysis.id,
            reason=reason,
        ))
        self.session.flush()
        return ActivationResult(pointer, previous)

    def rollback(
        self, *, tenant_id: str, asset_id: str, metadata_profile_id: str,
        actor_id: str, reason: str | None = None,
        search_context: str = "search_v2",
    ) -> ActivationResult:
        self._lock_asset(tenant_id, asset_id)
        pointer = self.session.scalar(
            select(ActiveAssetAnalysisModel).where(
                ActiveAssetAnalysisModel.tenant_id == tenant_id,
                ActiveAssetAnalysisModel.asset_id == asset_id,
                ActiveAssetAnalysisModel.metadata_profile_id == metadata_profile_id,
                ActiveAssetAnalysisModel.search_context == search_context,
            ).with_for_update()
        )
        if pointer is None:
            raise AnalysisActivationError("no active analysis is selected")
        transition = self.session.scalar(
            select(ActiveAnalysisAuditModel).where(
                ActiveAnalysisAuditModel.tenant_id == tenant_id,
                ActiveAnalysisAuditModel.asset_id == asset_id,
                ActiveAnalysisAuditModel.metadata_profile_id == metadata_profile_id,
                ActiveAnalysisAuditModel.search_context == search_context,
                ActiveAnalysisAuditModel.analysis_id == pointer.analysis_id,
                ActiveAnalysisAuditModel.previous_analysis_id.is_not(None),
            ).order_by(
                ActiveAnalysisAuditModel.created_at.desc(),
                ActiveAnalysisAuditModel.id.desc(),
            ).limit(1)
        )
        if transition is None or transition.previous_analysis_id is None:
            raise AnalysisActivationError("no previous analysis is available")
        return self.activate(
            tenant_id=tenant_id, asset_id=asset_id,
            analysis_id=transition.previous_analysis_id,
            actor_id=actor_id, reason=reason, search_context=search_context, action="rollback",
        )

    def enqueue_rebuild_and_reindex(self, *, tenant_id: str, active: ActiveAssetAnalysisModel) -> tuple[str]:
        analysis = self.resolve(tenant_id, active.asset_id, active.metadata_profile_id, active.search_context)
        jobs = ProcessingRepository(self.session)
        projection = jobs.create_job(
            tenant_id=tenant_id, job_type="search_projection_build", entity_type="asset",
            entity_id=active.asset_id, idempotency_key=f"active:projection:{analysis.id}:{analysis.search_projection_version}",
            payload={
                "asset_id": active.asset_id,
                "analysis_id": analysis.id,
                "active_analysis_id": active.id,
                "metadata_profile_id": active.metadata_profile_id,
                "search_context": active.search_context,
            },
        )
        return (projection.id,)
