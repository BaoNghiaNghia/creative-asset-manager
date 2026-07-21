from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import JSON, and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.redaction import redact_url_queries
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.assets.model import AssetSourceLinkModel, SourceAssetModel
from app.modules.search.governance_model import ActiveAssetAnalysisModel
from app.modules.search.operations_model import (
    SearchOperationItemModel,
    SearchOperationRunModel,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SearchOperationRepository:
    def __init__(self, session: Session):
        self.session = session

    def create_run(
        self,
        *,
        tenant_id: str,
        operation_type: str,
        filters: dict,
        target_projection_version: str,
        page_size: int = 100,
        dry_run: bool = False,
    ) -> SearchOperationRunModel:
        if operation_type not in {
            "rebuild_projections",
            "reindex_assets",
            "rebuild_and_reindex",
        }:
            raise ValueError("unsupported search operation")
        if not tenant_id or not target_projection_version.strip():
            raise ValueError("tenant and target projection version are required")
        if page_size < 1 or page_size > 500:
            raise ValueError("page_size must be between 1 and 500")
        asset_ids = filters.get("asset_ids") or []
        if len(asset_ids) > 1_000:
            raise ValueError("at most 1000 explicit asset IDs are allowed")
        run = SearchOperationRunModel(
            tenant_id=tenant_id,
            operation_type=operation_type,
            filters_json=dict(filters),
            target_projection_version=target_projection_version,
            page_size=page_size,
            dry_run=dry_run,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def get_run(self, tenant_id: str, run_id: str) -> SearchOperationRunModel:
        run = self.session.scalar(
            select(SearchOperationRunModel).where(
                SearchOperationRunModel.id == run_id,
                SearchOperationRunModel.tenant_id == tenant_id,
            )
        )
        if run is None:
            raise LookupError(run_id)
        return run

    def request_cancellation(self, tenant_id: str, run_id: str) -> SearchOperationRunModel:
        run = self.get_run(tenant_id, run_id)
        if run.status not in {"completed", "failed", "cancelled"}:
            run.cancellation_requested = True
            run.updated_at = utcnow()
            self.session.flush()
        return run

    def refresh(self, run: SearchOperationRunModel) -> SearchOperationRunModel:
        self.session.refresh(run)
        return run

    def analysis_page(self, run: SearchOperationRunModel, *, require_active: bool = False) -> list[AssetAiAnalysisModel]:
        filters = run.filters_json or {}
        statement = select(AssetAiAnalysisModel).where(
            AssetAiAnalysisModel.tenant_id == run.tenant_id,
            AssetAiAnalysisModel.status == "completed",
            AssetAiAnalysisModel.metadata_json.is_not(None),
        )
        if require_active:
            statement = statement.join(
                ActiveAssetAnalysisModel,
                and_(
                    ActiveAssetAnalysisModel.tenant_id == AssetAiAnalysisModel.tenant_id,
                    ActiveAssetAnalysisModel.asset_id == AssetAiAnalysisModel.asset_id,
                    ActiveAssetAnalysisModel.metadata_profile_id == AssetAiAnalysisModel.metadata_profile_id,
                    ActiveAssetAnalysisModel.analysis_id == AssetAiAnalysisModel.id,
                    ActiveAssetAnalysisModel.search_context == "search_v2",
                ),
            )
        if filters.get("metadata_profile"):
            statement = statement.where(
                AssetAiAnalysisModel.metadata_profile == filters["metadata_profile"]
            )
        if filters.get("current_projection_version"):
            statement = statement.where(
                AssetAiAnalysisModel.search_projection_version
                == filters["current_projection_version"]
            )
        asset_ids = tuple(filters.get("asset_ids") or ())
        if asset_ids:
            statement = statement.where(AssetAiAnalysisModel.asset_id.in_(asset_ids))
        if filters.get("only_missing"):
            statement = statement.where(
                or_(
                    AssetAiAnalysisModel.search_projection.is_(None),
                    AssetAiAnalysisModel.search_projection == JSON.NULL,
                )
            )
        if filters.get("only_failed"):
            statement = statement.join(
                SearchOperationItemModel,
                and_(
                    SearchOperationItemModel.analysis_id == AssetAiAnalysisModel.id,
                    SearchOperationItemModel.run_id == run.id,
                    SearchOperationItemModel.status == "failed",
                ),
            )
        elif run.cursor_created_at is not None and run.cursor_analysis_id is not None:
            statement = statement.where(
                or_(
                    AssetAiAnalysisModel.created_at > run.cursor_created_at,
                    and_(
                        AssetAiAnalysisModel.created_at == run.cursor_created_at,
                        AssetAiAnalysisModel.id > run.cursor_analysis_id,
                    ),
                )
            )
        return list(
            self.session.scalars(
                statement.order_by(
                    AssetAiAnalysisModel.created_at,
                    AssetAiAnalysisModel.id,
                ).limit(run.page_size)
            )
        )

    def source_display(self, tenant_id: str, asset_id: str) -> tuple[str, str]:
        source = self.session.scalar(
            select(SourceAssetModel)
            .join(
                AssetSourceLinkModel,
                AssetSourceLinkModel.source_asset_id == SourceAssetModel.id,
            )
            .where(
                AssetSourceLinkModel.tenant_id == tenant_id,
                AssetSourceLinkModel.asset_id == asset_id,
                SourceAssetModel.tenant_id == tenant_id,
            )
            .order_by(SourceAssetModel.id)
            .limit(1)
        )
        if source is None:
            return "", ""
        path = source.source_metadata.get("path", "")
        return source.filename or "", path if isinstance(path, str) else ""

    def mark_item(
        self,
        run: SearchOperationRunModel,
        analysis: AssetAiAnalysisModel,
        *,
        status: str,
        error: Exception | None = None,
    ) -> SearchOperationItemModel:
        item = self.session.scalar(
            select(SearchOperationItemModel).where(
                SearchOperationItemModel.run_id == run.id,
                SearchOperationItemModel.analysis_id == analysis.id,
            )
        )
        if item is None:
            item = SearchOperationItemModel(
                run_id=run.id,
                tenant_id=run.tenant_id,
                analysis_id=analysis.id,
                asset_id=analysis.asset_id,
            )
            self.session.add(item)
        item.status = status
        item.last_error_code = type(error).__name__[:100] if error else None
        item.last_error_message = redact_url_queries(str(error)) if error else None
        item.updated_at = utcnow()
        item.completed_at = utcnow() if status in {"completed", "failed", "skipped"} else None
        self.session.flush()
        return item

    def checkpoint(
        self,
        run: SearchOperationRunModel,
        page: Sequence[AssetAiAnalysisModel],
        *,
        scanned: int,
        processed: int,
        succeeded: int,
        failed: int,
        skipped: int,
    ) -> None:
        del processed, succeeded, failed, skipped
        run.scanned_count += scanned
        counts = dict(
            self.session.execute(
                select(SearchOperationItemModel.status, func.count())
                .where(SearchOperationItemModel.run_id == run.id)
                .group_by(SearchOperationItemModel.status)
            ).all()
        )
        run.succeeded_count = int(counts.get("completed", 0))
        run.failed_count = int(counts.get("failed", 0))
        run.skipped_count = int(counts.get("skipped", 0))
        run.processed_count = (
            run.succeeded_count + run.failed_count + run.skipped_count
        )
        if page and not (run.filters_json or {}).get("only_failed"):
            run.cursor_created_at = page[-1].created_at
            run.cursor_analysis_id = page[-1].id
        run.updated_at = utcnow()
        self.session.flush()

    def mark_running(self, run: SearchOperationRunModel) -> None:
        if run.status == "pending":
            run.started_at = utcnow()
        run.status = "running"
        run.completed_at = None
        run.last_error_code = None
        run.last_error_message = None
        run.updated_at = utcnow()
        self.session.flush()

    def mark_terminal(
        self,
        run: SearchOperationRunModel,
        status: str,
        error: Exception | None = None,
    ) -> None:
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("invalid terminal status")
        run.status = status
        run.last_error_code = type(error).__name__[:100] if error else None
        run.last_error_message = redact_url_queries(str(error)) if error else None
        run.completed_at = utcnow()
        run.updated_at = utcnow()
        self.session.flush()
