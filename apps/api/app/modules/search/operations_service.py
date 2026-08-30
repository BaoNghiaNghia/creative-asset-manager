from __future__ import annotations

from collections.abc import Mapping

from app.modules.ai_metadata.projection import SearchProjectionBuilder
from app.modules.ai_metadata.projection_service import SearchProjectionService
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.search.index_types import SearchIndexDocument, SearchIndexProvider, build_search_index_document
from app.modules.search.source_index import SearchSourceIndexResolver
from app.modules.search.index_lifecycle import SearchIndexLifecycleService
from app.modules.search.operations_model import SearchOperationRunModel
from app.modules.search.operations_repository import SearchOperationRepository


class SearchMaintenanceService:
    def __init__(
        self,
        repository: SearchOperationRepository,
        builder: SearchProjectionBuilder,
        *,
        index_provider: SearchIndexProvider | None = None,
        projection_enabled: bool = False,
        index_enabled: bool = False,
        deterministic_active_analysis_enabled: bool = False,
        index_lifecycle_enabled: bool = False,
    ):
        self.repository = repository
        self.builder = builder
        self.index_provider = index_provider
        self.projection_enabled = projection_enabled
        self.index_enabled = index_enabled
        self.deterministic_active_analysis_enabled = deterministic_active_analysis_enabled
        self.index_lifecycle_enabled = index_lifecycle_enabled

    async def run(
        self,
        *,
        tenant_id: str,
        run_id: str,
        index_version: str | None = None,
    ) -> SearchOperationRunModel:
        run = self.repository.get_run(tenant_id, run_id)
        if run.status == "completed":
            return run
        rebuild = run.operation_type in {"rebuild_projections", "rebuild_and_reindex"}
        reindex = run.operation_type in {"reindex_assets", "rebuild_and_reindex"}
        if rebuild and not self.projection_enabled and not run.dry_run:
            raise RuntimeError("SEARCH_PROJECTION_ENABLED is false")
        if reindex and not run.dry_run:
            if not self.index_enabled:
                raise RuntimeError("Elasticsearch search is disabled")
            if self.index_provider is None:
                raise RuntimeError("Elasticsearch provider is required")
            if run.target_index is None:
                if not index_version:
                    raise ValueError("index_version is required for a new reindex run")
                run.target_index = await self.index_provider.create_index(index_version)
                if self.index_lifecycle_enabled:
                    SearchIndexLifecycleService(
                        self.repository.session, self.index_provider
                    ).register(
                        physical_index_name=run.target_index,
                        index_prefix=self._index_prefix(run.target_index),
                        index_version=index_version,
                        projection_version=run.target_projection_version,
                    )
                self.repository.session.commit()

        self.repository.mark_running(run)
        self.repository.session.commit()
        source_index_resolver = (
            SearchSourceIndexResolver(self.repository.session) if reindex else None
        )
        try:
            while True:
                self.repository.refresh(run)
                if run.cancellation_requested:
                    self.repository.mark_terminal(run, "cancelled")
                    self.repository.session.commit()
                    return run
                page = self.repository.analysis_page(
                    run, require_active=self.deterministic_active_analysis_enabled
                )
                if not page:
                    break
                if source_index_resolver is not None:
                    source_index_resolver.preload_assets(
                        tenant_id=run.tenant_id,
                        asset_ids=(analysis.asset_id for analysis in page),
                    )
                page_succeeded = 0
                page_failed = 0
                page_skipped = 0
                index_documents: list[tuple[object, SearchIndexDocument]] = []
                for analysis in page:
                    self.repository.mark_item(run, analysis, status="running")
                    try:
                        if run.dry_run:
                            self.repository.mark_item(run, analysis, status="skipped")
                            page_skipped += 1
                            continue
                        if rebuild:
                            SearchProjectionService(
                                AiMetadataRepository(self.repository.session),
                                self.builder,
                                enabled=True,
                            ).rebuild(analysis.id)
                        if reindex:
                            index_documents.append(
                                (analysis, self._index_document(run, analysis, source_index_resolver))
                            )
                        else:
                            self.repository.mark_item(run, analysis, status="completed")
                            page_succeeded += 1
                    except Exception as exc:
                        self.repository.mark_item(
                            run,
                            analysis,
                            status="failed",
                            error=exc,
                        )
                        page_failed += 1
                if index_documents:
                    try:
                        await self.index_provider.bulk_upsert_to_index(
                            [document for _, document in index_documents],
                            run.target_index,
                        )
                        for analysis, _ in index_documents:
                            self.repository.mark_item(
                                run,
                                analysis,
                                status="completed",
                            )
                            page_succeeded += 1
                    except Exception as exc:
                        for analysis, _ in index_documents:
                            self.repository.mark_item(
                                run,
                                analysis,
                                status="failed",
                                error=exc,
                            )
                            page_failed += 1
                self.repository.checkpoint(
                    run,
                    page,
                    scanned=len(page),
                    processed=len(page),
                    succeeded=page_succeeded,
                    failed=page_failed,
                    skipped=page_skipped,
                )
                self.repository.session.commit()
                if source_index_resolver is not None:
                    source_index_resolver.clear_page_cache()

            if reindex and not run.dry_run:
                if run.failed_count:
                    self.repository.mark_terminal(
                        run,
                        "failed",
                        RuntimeError(f"{run.failed_count} item(s) failed"),
                    )
                elif self.index_lifecycle_enabled:
                    record = SearchIndexLifecycleService(
                        self.repository.session, self.index_provider
                    ).register(
                        physical_index_name=run.target_index,
                        index_prefix=self._index_prefix(run.target_index),
                        index_version=index_version or "resumed",
                        projection_version=run.target_projection_version,
                    )
                    record.document_count = run.succeeded_count
                    self.repository.mark_terminal(run, "completed")
                else:
                    switch = await self.index_provider.switch_aliases(run.target_index)
                    run.alias_switch_json = {
                        "target_index": switch.target_index,
                        "previous_read_indices": list(switch.previous_read_indices),
                        "previous_write_indices": list(switch.previous_write_indices),
                    }
                    self.repository.mark_terminal(run, "completed")
            else:
                terminal = "failed" if run.failed_count else "completed"
                self.repository.mark_terminal(run, terminal)
            self.repository.session.commit()
            return run
        except Exception as exc:
            self.repository.session.rollback()
            run = self.repository.get_run(tenant_id, run_id)
            self.repository.mark_terminal(run, "failed", exc)
            self.repository.session.commit()
            raise
        finally:
            if source_index_resolver is not None:
                source_index_resolver.clear()

    def _index_document(
        self,
        run,
        analysis,
        source_index_resolver: SearchSourceIndexResolver,
    ) -> SearchIndexDocument:
        source = source_index_resolver.for_asset(
            tenant_id=run.tenant_id,
            asset_id=analysis.asset_id,
        )
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
    def _index_prefix(target_index: str) -> str:
        for generation in ("v2", "v3"):
            marker = f"-{generation}-"
            if marker in target_index:
                return target_index.split(marker, 1)[0]
        raise ValueError("target index has no supported versioned generation")
