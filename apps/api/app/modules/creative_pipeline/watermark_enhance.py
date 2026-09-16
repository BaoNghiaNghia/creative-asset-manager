from __future__ import annotations
import asyncio
import hashlib
import inspect
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.domain.processing.handlers import DeferredJobOutcome, JobHandlerResult
from app.modules.creative_pipeline.artifacts import ArtifactService
from app.modules.creative_pipeline.constants import ArtifactType, NodeType, NodeRunStatus
from app.modules.creative_pipeline.enhancement import (
    EnhancementPolicy, VideoEnhancementError, VideoEnhancementInput,
)
from app.modules.creative_pipeline.model import (
    ArtifactModel, GenerationRunModel, ListingTaskModel, NodeRunModel,
    PipelineRunModel, SourceGroupModel,
)
from app.modules.creative_pipeline.orchestrator import CreativePipelineOrchestrator
from app.modules.creative_pipeline.platforms import platform_profile
from app.modules.creative_pipeline.storage import PipelineStorageError, PipelineStorageUnsupported, ensure_pipeline_structure
from app.modules.creative_pipeline.video_staging import stage_video_artifact_to_temp
from app.modules.creative_pipeline.video_generation import VIDEO_PROVIDER_ORDER
from app.modules.creative_pipeline.video_output import RATIO_SLUGS

class WatermarkSmartEnhanceNodeHandler:
    def __call__(self, context):
        return asyncio.run(self._execute(context))

    async def _execute(self, context):
        if context.is_cancelled or context.shutdown_requested.is_set():
            return JobHandlerResult.cancelled()
        with context.dependencies.session_factory() as session:
            orch = CreativePipelineOrchestrator(session)
            try:
                node = orch.begin_node_execution(context.job.tenant_id, context.job.entity_id, context.job.id, context.job.lease_owner)
                run = session.scalar(select(PipelineRunModel).where(PipelineRunModel.tenant_id == context.job.tenant_id, PipelineRunModel.id == node.pipeline_run_id))
                listing = session.scalar(select(ListingTaskModel).where(ListingTaskModel.tenant_id == run.tenant_id, ListingTaskModel.id == run.listing_task_id)) if run else None
                group = session.scalar(select(SourceGroupModel).where(SourceGroupModel.tenant_id == listing.tenant_id, SourceGroupModel.id == listing.source_group_id)) if listing else None
                factory = context.dependencies.resources.get("creative_pipeline_storage_factory")
                if run is None or listing is None or group is None or factory is None:
                    return JobHandlerResult.non_retryable("pipeline_lineage_unavailable", "Pipeline lineage is unavailable.")
                registry = context.dependencies.resources.get("video_enhancement_provider_registry")
                provider = registry.get() if registry is not None else context.dependencies.resources.get("video_enhancement_provider")
                if provider is None:
                    return DeferredJobOutcome("creative_enhancement_provider_not_configured", "No enhancement provider is configured.", self._retry_at(600))
                configured_policy = context.dependencies.resources.get("creative_pipeline_enhancement_policy")
                configured_policy = configured_policy if configured_policy is not None else getattr(context.dependencies.settings, "CREATIVE_PIPELINE_ENHANCEMENT_POLICY", None)
                policy = EnhancementPolicy.from_config(configured_policy)
                if policy is None:
                    return DeferredJobOutcome("creative_enhancement_policy_not_configured", "Enhancement policy is not configured.", self._retry_at(600))
                gateway = factory(run.tenant_id, group.external_source_id)
                pipeline = await ensure_pipeline_structure(listing, gateway)
                enhance_root = next(item for item in await gateway.list_children(pipeline.id) if item.name == "Watermark & Smart Enhance" and item.kind == "folder")
                raw_rows = session.scalars(select(ArtifactModel).join(
                    GenerationRunModel, (GenerationRunModel.tenant_id == ArtifactModel.tenant_id) &
                    (GenerationRunModel.id == ArtifactModel.generation_run_id)
                ).where(
                    ArtifactModel.tenant_id == run.tenant_id,
                    ArtifactModel.pipeline_run_id == run.id,
                    ArtifactModel.artifact_type == ArtifactType.RAW_VIDEO.value,
                    ArtifactModel.status == "available",
                    GenerationRunModel.status == "completed",
                )).all()
                order = {ratio: i for i, ratio in enumerate(platform_profile(listing.platform).required_aspect_ratios)}
                provider_order = {name: i for i, name in enumerate(VIDEO_PROVIDER_ORDER)}
                raw_rows = sorted(raw_rows, key=lambda row: (order.get(row.aspect_ratio, 999), provider_order.get(row.variant_key, 999), row.version))
                expected_count = len(platform_profile(listing.platform).required_aspect_ratios) * len(VIDEO_PROVIDER_ORDER)
                if not raw_rows:
                    return DeferredJobOutcome("creative_raw_video_not_ready", "No completed raw video artifacts are available.", self._retry_at(30))
                if len(raw_rows) != expected_count:
                    return DeferredJobOutcome("creative_raw_video_incomplete", "The complete raw video branch set is not available yet.", self._retry_at(30))
                max_bytes = self._max_output_bytes(context)
                for raw in raw_rows:
                    if context.is_cancelled or context.shutdown_requested.is_set():
                        return JobHandlerResult.cancelled()
                    if not raw.external_file_id or raw.mime_type != "video/mp4" or not raw.content_hash or not raw.size_bytes or raw.size_bytes <= 0:
                        raw.status = "inconsistent"
                        session.commit()
                        return JobHandlerResult.non_retryable("creative_raw_artifact_inconsistent", "Raw video artifact metadata is inconsistent.")
                    ratio_slug = RATIO_SLUGS.get(raw.aspect_ratio or "")
                    if ratio_slug is None:
                        return JobHandlerResult.non_retryable("creative_raw_artifact_inconsistent", "Raw video aspect ratio is unsupported.")
                    enhanced_path = f"Pipeline/Watermark & Smart Enhance/{ratio_slug}/v{raw.version:03d}_enhanced.mp4"
                    enhanced = ArtifactService(session).reserve_artifact(
                        tenant_id=run.tenant_id, pipeline_run_id=run.id, node_run_id=node.id,
                        generation_run_id=raw.generation_run_id, artifact_type=ArtifactType.ENHANCED_VIDEO,
                        version=raw.version, aspect_ratio=raw.aspect_ratio, variant_key=raw.variant_key,
                        relative_path=enhanced_path,
                    )
                    enhanced.model_provider, enhanced.model_name = raw.model_provider, raw.model_name
                    enhanced._artifact_parent_id = next(item for item in await gateway.list_children(enhance_root.id) if item.name == ratio_slug and item.kind == "folder").id
                    existing_meta = enhanced.metadata_json or {}
                    if existing_meta.get("raw_content_hash") and existing_meta.get("raw_content_hash") != raw.content_hash:
                        raise VideoEnhancementError("enhancement_raw_lineage_mismatch", "Enhanced artifact provenance does not match the current raw artifact.")
                    if enhanced.status == "available" and enhanced.external_file_id:
                        if await self._physical_artifact_valid(gateway, enhanced, max_bytes):
                            continue
                        enhanced.status = "inconsistent"
                    try:
                        async with stage_video_artifact_to_temp(gateway, raw, max_bytes) as staged:
                            request = VideoEnhancementInput(
                                tenant_id=run.tenant_id, raw_artifact_id=raw.id, raw_content_hash=raw.content_hash,
                                input_path=staged.path, aspect_ratio=raw.aspect_ratio, policy=policy,
                                idempotency_key=f"creative_pipeline:enhance:{raw.id}",
                            )
                            result = provider.enhance(request)
                            result = await result if inspect.isawaitable(result) else result
                            if result is None:
                                raise VideoEnhancementError("creative_enhancement_failed", "Enhancement provider returned no result.", retryable=True)
                            await ArtifactService(session).materialize_video(enhanced, gateway, result, max_output_bytes=max_bytes)
                            enhanced.metadata_json = {
                                "raw_artifact_id": raw.id, "raw_content_hash": raw.content_hash,
                                "raw_version": raw.version, "generation_run_id": raw.generation_run_id,
                                "provider": raw.model_provider or raw.variant_key, "model": raw.model_name,
                                "aspect_ratio": raw.aspect_ratio, "enhancement_policy_version": policy.version,
                                "enhancement_engine": result.engine, "enhancement_engine_version": result.engine_version,
                                "enhancement_idempotency_key": request.idempotency_key,
                                "metadata": dict(result.metadata or {}),
                            }
                            session.commit()
                    except PipelineStorageError as exc:
                        raw.status = "inconsistent"
                        session.commit()
                        raise exc
                orch.complete_node(run.tenant_id, node.id, context.job.id, context.job.lease_owner, output_version=f"generation_{int((raw_rows[0].metadata_json or {}).get('generation_number', 1)):03d}")
                session.commit()
                return JobHandlerResult.completed()
            except VideoEnhancementError as exc:
                session.rollback()
                if exc.code in {"enhancement_raw_lineage_mismatch", "input_corrupt", "unsupported_media"} or not exc.retryable:
                    return JobHandlerResult.non_retryable(exc.code, str(exc))
                return JobHandlerResult.retryable(exc.code, str(exc))
            except PipelineStorageError as exc:
                session.rollback()
                if str(exc) in {"creative_raw_artifact_inconsistent", "artifact_name_collision", "creative_video_output_unsupported", "creative_video_output_too_large", "creative_video_output_checksum_mismatch"}:
                    return JobHandlerResult.non_retryable(str(exc), str(exc))
                return JobHandlerResult.retryable("creative_enhancement_materialization_failed", str(exc))
            except (TimeoutError, ConnectionError) as exc:
                session.rollback()
                return JobHandlerResult.retryable("creative_enhancement_transient", str(exc))
            except Exception:
                session.rollback()
                return JobHandlerResult.retryable("creative_enhancement_failed", "Creative enhancement failed.")

    @staticmethod
    async def _physical_artifact_valid(gateway, artifact, max_bytes):
        if await gateway.get_item(artifact.external_file_id) is None:
            return False
        try:
            async with stage_video_artifact_to_temp(gateway, artifact, max_bytes):
                return True
        except (PipelineStorageError, PipelineStorageUnsupported):
            return False

    @staticmethod
    def _max_output_bytes(context):
        value = getattr(context.dependencies.settings, "CREATIVE_PIPELINE_ENHANCE_MAX_OUTPUT_BYTES", None)
        if value is None:
            value = getattr(context.dependencies.settings, "VIDEO_GENERATION_MAX_OUTPUT_BYTES", 256 * 1024 * 1024)
        value = int(value)
        if value <= 0:
            raise ValueError("creative enhancement output limit must be positive")
        return value

    @staticmethod
    def _retry_at(seconds):
        return datetime.now(timezone.utc) + timedelta(seconds=seconds)
