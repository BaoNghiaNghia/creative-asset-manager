from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.domain.processing.handlers import DeferredJobOutcome, JobHandlerContext, JobHandlerResult
from app.modules.assets.model import ExternalSourceModel
from app.modules.creative_pipeline.artifacts import ArtifactService
from app.modules.creative_pipeline.constants import ArtifactType, NodeType, ListingTaskStatus
from app.modules.creative_pipeline.model import ListingTaskModel, NodeRunModel, PipelineRunModel, SourceGroupModel
from app.modules.creative_pipeline.orchestrator import CreativePipelineOrchestrator, CREATIVE_PIPELINE_ENTITY_TYPE
from app.modules.creative_pipeline.storage import PipelineStorageError, PipelineStorageUnsupported, ensure_pipeline_structure


class CreativePipelineNodeHandler:
    """Executes only input_data; future nodes are deferred without attempts."""
    def __init__(self, settings=None):
        self.settings = settings

    def __call__(self, context: JobHandlerContext):
        node_type = str(context.job.payload.get("node_type") or "")
        if node_type != NodeType.INPUT_DATA.value:
            return DeferredJobOutcome(
                "creative_pipeline_node_not_implemented_yet",
                "Creative Pipeline node is not implemented in this release.",
                datetime.now(timezone.utc) + timedelta(minutes=10),
            )
        return asyncio.run(self._execute(context))

    async def _execute(self, context: JobHandlerContext):
        if context.is_cancelled or context.shutdown_requested.is_set():
            return JobHandlerResult.cancelled()
        with context.dependencies.session_factory() as session:
            orch = CreativePipelineOrchestrator(session)
            try:
                node = orch.begin_node_execution(
                    context.job.tenant_id, context.job.entity_id, context.job.id, context.job.lease_owner
                )
                run = session.scalar(select(PipelineRunModel).where(PipelineRunModel.tenant_id == context.job.tenant_id, PipelineRunModel.id == node.pipeline_run_id))
                listing = session.scalar(select(ListingTaskModel).where(ListingTaskModel.tenant_id == context.job.tenant_id, ListingTaskModel.id == run.listing_task_id))
                group = session.scalar(select(SourceGroupModel).where(SourceGroupModel.tenant_id == context.job.tenant_id, SourceGroupModel.id == listing.source_group_id))
                source = session.scalar(select(ExternalSourceModel).where(ExternalSourceModel.tenant_id == context.job.tenant_id, ExternalSourceModel.id == group.external_source_id))
                if source is None or source.status != "active" or listing.status in {ListingTaskStatus.MISSING_SOURCE.value, ListingTaskStatus.ARCHIVED.value}:
                    return JobHandlerResult.non_retryable("input_source_unavailable", "Listing source is unavailable.")
                factory = context.dependencies.resources.get("creative_pipeline_storage_factory")
                if factory is None:
                    return JobHandlerResult.non_retryable("pipeline_storage_write_unsupported", "Pipeline storage is unavailable.")
                gateway = factory(context.job.tenant_id, source.id)
                if hasattr(gateway, "__aenter__"):
                    async with gateway:
                        return await self._collect(session, orch, listing, group, run, node, gateway, context)
                return await self._collect(session, orch, listing, group, run, node, gateway, context)
            except PipelineStorageUnsupported as exc:
                session.rollback()
                return JobHandlerResult.non_retryable("pipeline_storage_write_unsupported", str(exc))
            except PipelineStorageError as exc:
                session.rollback()
                return JobHandlerResult.non_retryable("pipeline_storage_inconsistent", str(exc))
            except (TimeoutError, ConnectionError) as exc:
                session.rollback()
                return JobHandlerResult.retryable("pipeline_storage_transient", str(exc))
            except Exception:
                session.rollback()
                return JobHandlerResult.retryable("creative_pipeline_input_failed", "Input collection failed.")

    async def _collect(self, session, orch, listing, group, run, node, gateway, context):
        if context.is_cancelled:
            return JobHandlerResult.cancelled()
        pipeline = await ensure_pipeline_structure(listing, gateway)
        direct = await gateway.list_children(listing.external_folder_id)
        source_folders = [item for item in direct if item.name == "Source" and item.kind == "folder"]
        ugc_folders = [item for item in direct if item.name == "UGC - Macro Vid" and item.kind == "folder"]
        if len(source_folders) != 1:
            return JobHandlerResult.non_retryable("input_source_folder_ambiguous" if source_folders else "input_source_folder_missing", "Source folder is unavailable or ambiguous.")
        if len(ugc_folders) > 1:
            return JobHandlerResult.non_retryable("input_ugc_folder_ambiguous", "UGC folder is ambiguous.")
        source_items = await gateway.list_children(source_folders[0].id)
        ugc_items = await gateway.list_children(ugc_folders[0].id) if ugc_folders else []
        def safe(items, role):
            return [
                {"external_asset_id": item.id, "name": item.name, "kind": item.kind, "mime_type": getattr(item, "mime_type", None), "size": getattr(item, "size", None), "modified_at": getattr(item, "modified_at", None), "source_folder_role": role}
                for item in items if item.kind != "folder"
            ]
        ratios = ["1:1"] if listing.platform == "etsy" else ["16:9", "9:16"]
        snapshot = {
            "schema_version": 1, "platform": listing.platform, "listing_key": listing.listing_key,
            "source_group": {"id": group.id, "name": group.name, "platform": group.platform, "external_source_id": group.external_source_id},
            "listing": {"id": listing.id, "external_folder_id": listing.external_folder_id, "folder_name": listing.folder_name},
            "required_aspect_ratios": ratios, "source_assets": safe(source_items, "source"), "ugc_macro_video_assets": safe(ugc_items, "ugc_macro_vid"),
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }
        artifacts = ArtifactService(session)
        snapshot_artifact = artifacts.reserve_artifact(tenant_id=listing.tenant_id, pipeline_run_id=run.id, node_run_id=node.id, artifact_type=ArtifactType.INPUT_SNAPSHOT, version=1)
        manifest_artifact = artifacts.reserve_artifact(tenant_id=listing.tenant_id, pipeline_run_id=run.id, node_run_id=node.id, artifact_type=ArtifactType.INPUT_MANIFEST, version=1)
        input_folder = next(item for item in await gateway.list_children(pipeline.id) if item.name == "Input" and item.kind == "folder")
        snapshot_artifact._artifact_parent_id = input_folder.id
        manifest_artifact._artifact_parent_id = input_folder.id
        snapshot_done = await artifacts.materialize_json(snapshot_artifact, gateway, snapshot)
        manifest = {
            "schema_version": 1, "input_snapshot_version": 1, "input_snapshot_sha256": snapshot_done.content_hash,
            "source_asset_count": len(snapshot["source_assets"]), "ugc_asset_count": len(snapshot["ugc_macro_video_assets"]),
            "source_folder_id": source_folders[0].id, "ugc_folder_id": ugc_folders[0].id if ugc_folders else None,
            "required_aspect_ratios": ratios, "captured_at": snapshot["captured_at"],
        }
        await artifacts.materialize_json(manifest_artifact, gateway, manifest)
        orch.complete_node(listing.tenant_id, node.id, context.job.id, context.job.lease_owner, output_version="v001")
        session.commit()
        return JobHandlerResult.completed()
