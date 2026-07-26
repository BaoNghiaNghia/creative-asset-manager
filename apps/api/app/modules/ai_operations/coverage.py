from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.modules.ai_governance.model import AiBudgetEventModel
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.assets.model import AssetModel
from app.modules.processing.model import ProcessingJobModel


class SearchCoverageSummaryService:
    """Fast database-only coverage summary for the Operations dashboard.

    Elasticsearch is intentionally never queried here.  A full alias check is
    an explicit administrator audit and its latest result is retained as an
    audit event.
    """

    def __init__(self, session: Session, *, projection_version: str):
        self.session = session
        self.projection_version = projection_version

    def summary(self, *, tenant_id: str) -> dict[str, Any]:
        newer = aliased(AssetAiAnalysisModel)
        latest = ~exists(select(1).where(
            newer.tenant_id == AssetAiAnalysisModel.tenant_id,
            newer.asset_id == AssetAiAnalysisModel.asset_id,
            newer.status == "completed",
            or_(
                newer.created_at > AssetAiAnalysisModel.created_at,
                and_(newer.created_at == AssetAiAnalysisModel.created_at, newer.id > AssetAiAnalysisModel.id),
            ),
        ))
        analyses = (
            select(AssetAiAnalysisModel)
            .join(AssetModel, and_(AssetModel.id == AssetAiAnalysisModel.asset_id, AssetModel.tenant_id == AssetAiAnalysisModel.tenant_id))
            .where(AssetAiAnalysisModel.tenant_id == tenant_id, AssetAiAnalysisModel.status == "completed", latest)
            .subquery()
        )
        rows = self.session.execute(select(
            analyses.c.asset_id, analyses.c.id, analyses.c.search_projection,
            analyses.c.search_projection_version,
        )).all()
        asset_ids = [str(row.asset_id) for row in rows]
        completed_jobs: set[str] = set()
        failed_jobs: set[str] = set()
        pending_jobs: set[str] = set()
        if asset_ids:
            jobs = self.session.execute(select(
                ProcessingJobModel.entity_id, ProcessingJobModel.status
            ).where(
                ProcessingJobModel.tenant_id == tenant_id,
                ProcessingJobModel.job_type == "asset_index",
                ProcessingJobModel.entity_type == "asset",
                ProcessingJobModel.entity_id.in_(asset_ids),
            )).all()
            for asset_id, status in jobs:
                if status == "completed": completed_jobs.add(str(asset_id))
                elif status == "failed": failed_jobs.add(str(asset_id))
                elif status in {"pending", "processing", "retry"}: pending_jobs.add(str(asset_id))

        counts: Counter[str] = Counter()
        for row in rows:
            asset_id = str(row.asset_id)
            if not isinstance(row.search_projection, dict) or not row.search_projection_version:
                counts["projection_missing"] += 1
            elif row.search_projection_version != self.projection_version:
                counts["projection_stale"] += 1
            elif asset_id in completed_jobs:
                counts["v3_indexed_documents"] += 1
            elif asset_id in failed_jobs:
                counts["search_failed"] += 1
            else:
                counts["indexing_backlog"] += 1
        event = self.session.scalar(select(AiBudgetEventModel).where(
            AiBudgetEventModel.tenant_id == tenant_id,
            AiBudgetEventModel.action == "search_coverage_audit",
        ).order_by(AiBudgetEventModel.created_at.desc(), AiBudgetEventModel.id.desc()))
        details = event.details_json if event and isinstance(event.details_json, dict) else {}
        document_missing = int(details.get("database_indexed_document_missing", 0) or 0)
        completed = len(rows)
        return {
            "completed_analysis_assets": completed,
            "current_projection_assets": max(0, completed - counts["projection_missing"] - counts["projection_stale"]),
            "v3_indexed_documents": counts["v3_indexed_documents"],
            "projection_missing": counts["projection_missing"],
            "projection_stale": counts["projection_stale"],
            "indexing_backlog": counts["indexing_backlog"] + len(pending_jobs - completed_jobs),
            "search_failed": counts["search_failed"],
            "database_indexed_document_missing": document_missing,
            "coverage_percent": (counts["v3_indexed_documents"] / completed * 100) if completed else 0.0,
            "last_audited_at": event.created_at if event else None,
            "elasticsearch_verification_included": bool(details.get("verify_elasticsearch", False)),
            "repair_jobs": self.repair_jobs(tenant_id=tenant_id),
        }

    def repair_jobs(self, *, tenant_id: str) -> dict[str, int]:
        rows = self.session.execute(select(ProcessingJobModel.status, func.count()).where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.idempotency_key.like("coverage:%"),
            ProcessingJobModel.job_type.in_(("search_projection_build", "asset_index")),
        ).group_by(ProcessingJobModel.status)).all()
        counts = {str(status): int(value) for status, value in rows}
        return {
            "queued": counts.get("pending", 0) + counts.get("retry", 0),
            "running": counts.get("processing", 0),
            "completed": counts.get("completed", 0),
            "failed": counts.get("failed", 0),
        }
