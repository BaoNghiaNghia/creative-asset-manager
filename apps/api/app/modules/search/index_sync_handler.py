from __future__ import annotations

import asyncio
from collections.abc import Mapping

from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import JobHandlerContext, JobHandlerResult
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3RequestError
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.search.governance_model import ActiveAssetAnalysisModel
from app.modules.search.index_types import (
    SearchIndexDocument,
    SearchIndexProvider,
    build_search_index_document,
)
from app.modules.search.source_index import SearchSourceIndexResolver


class SearchIndexSyncJobHandler:
    """Repair a single asset document after a committed source-state change.

    The source-sync transaction only creates this job. Elasticsearch work occurs
    later through the normal retrying worker, so a transient ES outage cannot
    roll back source deletion or leave it unaccounted for.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult:
        settings = self.settings or get_settings()
        if not (settings.ELASTICSEARCH_V2_ENABLED or settings.SEARCH_V3_ENABLED):
            return JobHandlerResult.non_retryable(
                "elasticsearch_search_disabled",
                "Elasticsearch search is disabled.",
            )
        provider = context.dependencies.resources.get("search_index_provider")
        if provider is None:
            return JobHandlerResult.non_retryable(
                "search_index_unconfigured",
                "Search index provider is not configured.",
            )
        asset_id = str(
            context.job.payload.get("asset_id") or context.job.entity_id or ""
        ).strip()
        if not asset_id:
            return JobHandlerResult.non_retryable(
                "invalid_search_index_sync_job",
                "asset_id is required.",
            )
        try:
            with context.dependencies.session_factory() as session:
                document = self._live_document(
                    session,
                    tenant_id=context.job.tenant_id,
                    asset_id=asset_id,
                    deterministic_active=(
                        settings.DETERMINISTIC_ACTIVE_ANALYSIS_ENABLED
                    ),
                )
            self._run(provider, document=document, asset_id=asset_id, context=context)
            return JobHandlerResult.completed()
        except ElasticsearchV3RequestError as exc:
            if (
                exc.status_code is not None
                and 400 <= exc.status_code < 500
                and exc.status_code != 429
            ):
                return JobHandlerResult.non_retryable(
                    "search_index_sync_rejected", str(exc)
                )
            return JobHandlerResult.retryable(
                "search_index_sync_unavailable", str(exc)
            )
        except Exception as exc:
            return JobHandlerResult.retryable("search_index_sync_failed", str(exc))

    @staticmethod
    def _run(
        provider: SearchIndexProvider,
        *,
        document: SearchIndexDocument | None,
        asset_id: str,
        context: JobHandlerContext,
    ) -> None:
        operation = (
            provider.bulk_upsert((document,))
            if document is not None
            else provider.delete_documents((asset_id,))
        )
        executor = context.dependencies.resources.get("async_executor")
        if executor is None:
            asyncio.run(operation)
        else:
            executor.run(operation)

    @staticmethod
    def _live_document(
        session,
        *,
        tenant_id: str,
        asset_id: str,
        deterministic_active: bool,
    ) -> SearchIndexDocument | None:
        source = SearchSourceIndexResolver(session).for_asset(
            tenant_id=tenant_id,
            asset_id=asset_id,
        )
        if not source.source_id:
            return None
        analysis = SearchIndexSyncJobHandler._analysis(
            session,
            tenant_id=tenant_id,
            asset_id=asset_id,
            deterministic_active=deterministic_active,
        )
        if analysis is None:
            return None
        return build_search_index_document(
            analysis,
            source_id=source.source_id,
            parent_id=source.parent_id,
            ancestor_ids=source.ancestor_ids,
            filename=source.filename,
            folder_path=source.folder_path,
            media_kind=source.media_kind,
            mime_type=source.mime_type,
            extension=source.extension,
            source_provider=source.source_provider,
            source_created_at=source.source_created_at,
            source_modified_at=source.source_modified_at,
            width=source.width,
            height=source.height,
            duration_ms=source.duration_ms,
            file_size_bytes=source.file_size_bytes,
        )

    @staticmethod
    def _analysis(
        session,
        *,
        tenant_id: str,
        asset_id: str,
        deterministic_active: bool,
    ) -> AssetAiAnalysisModel | None:
        if deterministic_active:
            pointer = session.scalar(
                select(ActiveAssetAnalysisModel).where(
                    ActiveAssetAnalysisModel.tenant_id == tenant_id,
                    ActiveAssetAnalysisModel.asset_id == asset_id,
                    ActiveAssetAnalysisModel.search_context == "search_v2",
                )
            )
            analysis = (
                session.get(AssetAiAnalysisModel, pointer.analysis_id)
                if pointer is not None
                else None
            )
        else:
            analysis = session.scalar(
                select(AssetAiAnalysisModel)
                .where(
                    AssetAiAnalysisModel.tenant_id == tenant_id,
                    AssetAiAnalysisModel.asset_id == asset_id,
                    AssetAiAnalysisModel.status == "completed",
                )
                .order_by(AssetAiAnalysisModel.completed_at.desc())
                .limit(1)
            )
        if (
            analysis is None
            or analysis.status != "completed"
            or analysis.validation_errors_json
            or not isinstance(analysis.search_projection, Mapping)
            or not analysis.search_projection_version
        ):
            return None
        return analysis
