from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from sqlalchemy import select
from app.domain.processing.handlers import DeferredJobOutcome, JobHandlerResult
from app.modules.creative_pipeline.artifacts import ArtifactService
from app.modules.creative_pipeline.constants import ArtifactType, GenerationRunStatus
from app.modules.creative_pipeline.model import ArtifactModel, GenerationRunModel, ListingTaskModel, PipelineRunModel, SourceGroupModel
from app.modules.creative_pipeline.orchestrator import CreativePipelineOrchestrator
from app.modules.creative_pipeline.platforms import platform_profile
from app.modules.creative_pipeline.storage import PipelineStorageError, ensure_pipeline_structure
from app.modules.creative_pipeline.video_generation import (
    VIDEO_PROVIDER_ORDER, VideoGenerationCancelInput, VideoGenerationPollInput,
    VideoGenerationProviderError,
)

RATIO_SLUGS = {"1:1": "1x1", "16:9": "16x9", "9:16": "9x16"}

def raw_artifact_version(generation_number: int, provider: str) -> int:
    if generation_number <= 0 or provider not in VIDEO_PROVIDER_ORDER:
        raise ValueError("invalid generation branch")
    return (generation_number - 1) * len(VIDEO_PROVIDER_ORDER) + VIDEO_PROVIDER_ORDER.index(provider) + 1



class VideoOutputNodeHandler:
    def __call__(self, context):
        return asyncio.run(self._execute(context))

    async def _execute(self, context):
        if context.is_cancelled or context.shutdown_requested.is_set():
            return JobHandlerResult.cancelled()
        registry = context.dependencies.resources.get("video_generation_provider_registry")
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
                gateway = factory(run.tenant_id, group.external_source_id)
                pipeline = await ensure_pipeline_structure(listing, gateway)
                output_root = next(item for item in await gateway.list_children(pipeline.id) if item.name == "Video Output" and item.kind == "folder")
                profile = platform_profile(listing.platform)
                expected = []
                for ratio in profile.required_aspect_ratios:
                    for provider in VIDEO_PROVIDER_ORDER:
                        model = self._model(context, provider)
                        if not model:
                            return DeferredJobOutcome("creative_video_generation_config_missing", "Video generation model configuration is unavailable.", self._retry_at(600))
                        generation = session.scalar(select(GenerationRunModel).where(
                            GenerationRunModel.tenant_id == run.tenant_id,
                            GenerationRunModel.pipeline_run_id == run.id,
                            GenerationRunModel.provider == provider,
                            GenerationRunModel.model == model,
                            GenerationRunModel.aspect_ratio == ratio,
                            GenerationRunModel.generation_number == 1,
                        ))
                        if generation is None or generation.status != GenerationRunStatus.COMPLETED.value or not generation.provider_request_id or not generation.prompt_artifact_id:
                            return DeferredJobOutcome("creative_video_generation_incomplete", "Required completed video generation branches are not ready.", self._retry_at(30))
                        prompt_artifact = session.scalar(select(ArtifactModel).where(
                            ArtifactModel.tenant_id == run.tenant_id,
                            ArtifactModel.id == generation.prompt_artifact_id,
                            ArtifactModel.pipeline_run_id == run.id,
                            ArtifactModel.artifact_type == ArtifactType.PROMPT.value,
                            ArtifactModel.status == "available",
                        ))
                        if prompt_artifact is None:
                            return JobHandlerResult.non_retryable("prompt_artifact_provenance_mismatch", "GenerationRun prompt lineage is invalid.")
                        expected.append((ratio, provider, model, generation, prompt_artifact))
                for ratio, provider, model, generation, prompt_artifact in expected:
                    version = raw_artifact_version(generation.generation_number, provider)
                    path = f"Pipeline/Video Output/{RATIO_SLUGS[ratio]}/v{version:03d}.mp4"
                    artifact = ArtifactService(session).reserve_artifact(
                        tenant_id=run.tenant_id, pipeline_run_id=run.id, node_run_id=node.id,
                        generation_run_id=generation.id, artifact_type=ArtifactType.RAW_VIDEO,
                        version=version, aspect_ratio=ratio, variant_key=provider, relative_path=path,
                    )
                    artifact.model_provider = provider
                    artifact.model_name = model
                    ratio_folder = next(item for item in await gateway.list_children(output_root.id) if item.name == RATIO_SLUGS[ratio] and item.kind == "folder")
                    artifact._artifact_parent_id = ratio_folder.id
                    if artifact.status == "available" and artifact.external_file_id and await gateway.get_item(artifact.external_file_id) is not None:
                        continue
                    if registry is None or registry.get(provider) is None:
                        return DeferredJobOutcome("creative_video_provider_not_configured", "Video provider is not configured.", self._retry_at(600))
                    provider_adapter = registry.require(provider)
                    result = await provider_adapter.fetch_result(VideoGenerationPollInput(run.tenant_id, generation.provider_request_id))
                    max_bytes = int(getattr(context.dependencies.settings, "VIDEO_GENERATION_MAX_OUTPUT_BYTES", 256 * 1024 * 1024))
                    await ArtifactService(session).materialize_video(artifact, gateway, result, max_output_bytes=max_bytes)
                    artifact.metadata_json = {
                        "generation_run_id": generation.id,
                        "generation_number": generation.generation_number,
                        "provider": provider,
                        "model": model,
                        "provider_request_id": generation.provider_request_id,
                        "prompt_artifact_id": prompt_artifact.id,
                        "aspect_ratio": ratio,
                        "raw_artifact_version": version,
                        "knowledge_snapshot_id": run.knowledge_snapshot_id,
                        "provider_checksum": result.checksum,
                    }
                    session.commit()
                orch.complete_node(run.tenant_id, node.id, context.job.id, context.job.lease_owner, output_version="v001")
                session.commit()
                return JobHandlerResult.completed()
            except VideoGenerationProviderError as exc:
                session.rollback()
                if exc.retryable:
                    return JobHandlerResult.retryable(exc.code, str(exc))
                return JobHandlerResult.non_retryable(exc.code, str(exc))
            except PipelineStorageError as exc:
                session.rollback()
                if exc.args and exc.args[0] in {"creative_video_output_too_large", "creative_video_output_unsupported", "creative_video_output_checksum_mismatch", "artifact_name_collision"}:
                    return JobHandlerResult.non_retryable(str(exc), str(exc))
                return JobHandlerResult.retryable("creative_video_output_materialization_failed", str(exc))
            except (TimeoutError, ConnectionError) as exc:
                session.rollback()
                return JobHandlerResult.retryable("creative_video_output_transient", str(exc))
            except Exception:
                session.rollback()
                return JobHandlerResult.retryable("creative_video_output_failed", "Video output materialization failed.")

    @staticmethod
    def _model(context, provider):
        settings = context.dependencies.settings
        key = "creative_pipeline_seedance_model" if provider == "seedance" else "creative_pipeline_google_omni_model"
        setting = "CREATIVE_PIPELINE_SEEDANCE_MODEL" if provider == "seedance" else "CREATIVE_PIPELINE_GOOGLE_OMNI_MODEL"
        value = context.dependencies.resources.get(key) or getattr(settings, setting, None)
        return str(value) if value else None

    @staticmethod
    def _retry_at(seconds):
        from datetime import timedelta
        return datetime.now(timezone.utc) + timedelta(seconds=seconds)
