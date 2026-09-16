from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol

from app.domain.processing.handlers import DeferredJobOutcome, JobHandlerContext, JobHandlerResult
from app.modules.creative_pipeline.constants import ArtifactType, GenerationRunStatus, NodeType
from app.modules.creative_pipeline.model import ArtifactModel, GenerationRunModel, ListingTaskModel, PipelineRunModel, SourceGroupModel
from app.modules.creative_pipeline.orchestrator import CreativePipelineOrchestrator
from app.modules.creative_pipeline.lineage import CreativePipelineLineageResolver
from app.modules.creative_pipeline.platforms import platform_profile
from app.modules.creative_pipeline.prompt_generation import DurablePrompt
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError


VIDEO_PROVIDER_ORDER = ("seedance", "google_omni")
CANONICAL_STATES = frozenset({"submitted", "running", "completed", "failed", "cancelled"})
GENERATION_RUN_ALLOWED_TRANSITIONS = {
    "pending": frozenset({"submitted", "running", "retry_wait", "failed", "cancelled"}),
    "submitted": frozenset({"submitted", "running", "retry_wait", "completed", "failed", "cancelled"}),
    "running": frozenset({"running", "retry_wait", "completed", "failed", "cancelled"}),
    "retry_wait": frozenset({"submitted", "running", "retry_wait", "failed", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}


def transition_generation_run(run: GenerationRunModel, target: str) -> None:
    if target == run.status:
        return
    if target not in GENERATION_RUN_ALLOWED_TRANSITIONS.get(run.status, frozenset()):
        raise VideoGenerationProviderError(
            f"Illegal generation run transition: {run.status} -> {target}.",
            code="generation_state_invalid",
            retryable=False,
        )
    run.status = target


class VideoGenerationProviderError(RuntimeError):
    def __init__(self, message: str, *, code: str, retryable: bool, status_code: int | None = None, retry_after_seconds: int | None = None, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.details = dict(details or {})


@dataclass(frozen=True, slots=True)
class VideoGenerationSubmitInput:
    tenant_id: str
    generation_run_id: str
    provider: str
    model: str
    aspect_ratio: str
    prompt: str
    generation_parameters: Mapping[str, Any]
    idempotency_key: str
    references: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VideoGenerationSubmission:
    provider_request_id: str | None
    state: str
    idempotent_replay: bool = False
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VideoGenerationPollInput:
    tenant_id: str
    provider_request_id: str


@dataclass(frozen=True, slots=True)
class VideoGenerationStatus:
    state: str
    retry_after_seconds: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VideoGenerationCancelInput:
    tenant_id: str
    provider_request_id: str


@dataclass(frozen=True, slots=True)
class VideoGenerationResult:
    provider_request_id: str
    content_type: str | None = None
    content_length: int | None = None
    checksum: str | None = None
    result_handle: str | None = None
    content: Any | None = None


class VideoGenerationProvider(Protocol):
    provider_name: str

    async def submit(self, request: VideoGenerationSubmitInput) -> VideoGenerationSubmission: ...
    async def poll(self, request: VideoGenerationPollInput) -> VideoGenerationStatus: ...
    async def cancel(self, request: VideoGenerationCancelInput) -> VideoGenerationStatus: ...
    async def fetch_result(self, request: VideoGenerationPollInput) -> VideoGenerationResult: ...


class VideoGenerationProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, VideoGenerationProvider] = {}

    def register(self, provider: VideoGenerationProvider) -> None:
        name = self._validate(provider.provider_name)
        if name in self._providers:
            raise ValueError(f"video provider '{name}' is already registered")
        self._providers[name] = provider

    def get(self, name: str) -> VideoGenerationProvider | None:
        try:
            return self._providers.get(self._validate(name))
        except ValueError:
            return None

    def require(self, name: str) -> VideoGenerationProvider:
        provider = self.get(name)
        if provider is None:
            raise VideoGenerationProviderError("Video provider is not configured.", code="creative_video_provider_not_configured", retryable=False)
        return provider

    def list_capabilities(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    @staticmethod
    def _validate(name: str) -> str:
        if not isinstance(name, str) or not name or name.lower() != name or not name.replace("_", "").isalnum():
            raise ValueError("video provider names must be stable lowercase identifiers")
        return name


class _LogicalProviderAdapter:
    def __init__(self, provider_name: str, transport: Any | None = None):
        self.provider_name = provider_name
        self.transport = transport

    async def _call(self, method: str, request: Any):
        if self.transport is None:
            raise VideoGenerationProviderError("Provider transport contract is not configured.", code="invalid_configuration", retryable=False)
        fn = getattr(self.transport, method, None)
        if fn is None:
            raise VideoGenerationProviderError("Provider transport contract is incomplete.", code="invalid_configuration", retryable=False)
        result = fn(request)
        return await result if inspect.isawaitable(result) else result

    async def submit(self, request): return await self._call("submit", request)
    async def poll(self, request): return await self._call("poll", request)
    async def cancel(self, request): return await self._call("cancel", request)
    async def fetch_result(self, request): return await self._call("fetch_result", request)


class SeedanceVideoGenerationProvider(_LogicalProviderAdapter):
    def __init__(self, transport: Any | None = None):
        super().__init__("seedance", transport)


class GoogleOmniVideoGenerationProvider(_LogicalProviderAdapter):
    def __init__(self, transport: Any | None = None):
        super().__init__("google_omni", transport)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _maybe(value):
    return await value if inspect.isawaitable(value) else value


class CreativeVideoGenerationExecutor:
    provider_order = VIDEO_PROVIDER_ORDER

    def __init__(self, context: JobHandlerContext):
        self.context = context

    def _model(self, provider: str) -> str | None:
        resources = self.context.dependencies.resources
        settings = self.context.dependencies.settings
        key = "creative_pipeline_seedance_model" if provider == "seedance" else "creative_pipeline_google_omni_model"
        setting = "CREATIVE_PIPELINE_SEEDANCE_MODEL" if provider == "seedance" else "CREATIVE_PIPELINE_GOOGLE_OMNI_MODEL"
        value = resources.get(key) or getattr(settings, setting, None)
        return str(value) if value else None

    def _registry(self) -> VideoGenerationProviderRegistry | None:
        return self.context.dependencies.resources.get("video_generation_provider_registry")

    def _defer(self, code: str, message: str, seconds: int = 30):
        return DeferredJobOutcome(code, message, _now() + timedelta(seconds=max(5, min(seconds, 900))))

    async def _prompt_rows(self, session, run, listing, gateway):
        profile = platform_profile(listing.platform)
        result = []
        for provider in self.provider_order:
            model = self._model(provider)
            if not model:
                raise VideoGenerationProviderError("Video generation model is not configured.", code="creative_video_generation_config_missing", retryable=False)
            artifact = CreativePipelineLineageResolver(session).effective_prompt(run, provider)
            if artifact is None:
                raise VideoGenerationProviderError("Prompt artifact is unavailable.", code="prompt_artifact_unavailable", retryable=False)
            document = await self._read_json_artifact(artifact, gateway)
            try:
                durable = DurablePrompt.model_validate(document)
            except Exception as exc:
                raise VideoGenerationProviderError("Prompt artifact is invalid.", code="prompt_artifact_invalid", retryable=False) from exc
            ratios = [item.aspect_ratio for item in durable.outputs]
            if durable.platform != listing.platform or ratios != list(profile.required_aspect_ratios):
                raise VideoGenerationProviderError("Prompt artifact lineage is invalid.", code="prompt_artifact_provenance_mismatch", retryable=False)
            for output in durable.outputs:
                if output.provider != provider or output.model != model or output.knowledge_snapshot_id != run.knowledge_snapshot_id or not output.prompt.strip():
                    raise VideoGenerationProviderError("Prompt artifact lineage is invalid.", code="prompt_artifact_provenance_mismatch", retryable=False)
                result.append((provider, model, output, artifact))
        return result

    async def _read_json_artifact(self, artifact, gateway):
        if artifact is None or not artifact.external_file_id:
            raise VideoGenerationProviderError("Prompt artifact content is unavailable.", code="prompt_artifact_unavailable", retryable=False)
        body = await gateway.download_bytes(artifact.external_file_id)
        import json
        return json.loads(body.decode("utf-8"))

    def _ensure_runs(self, session, run, rows):
        output = []
        cycle = session.scalar(select(func.max(GenerationRunModel.generation_number)).where(GenerationRunModel.tenant_id == run.tenant_id, GenerationRunModel.pipeline_run_id == run.id)) or (int(session.scalar(select(func.max(GenerationRunModel.generation_number)).where(GenerationRunModel.tenant_id == run.tenant_id, GenerationRunModel.listing_task_id == run.listing_task_id)) or 0) + 1)
        for provider, model, prompt, artifact in rows:
            existing = session.scalar(select(GenerationRunModel).where(
                GenerationRunModel.tenant_id == run.tenant_id,
                GenerationRunModel.pipeline_run_id == run.id,
                GenerationRunModel.listing_task_id == run.listing_task_id,
                GenerationRunModel.provider == provider,
                GenerationRunModel.model == model,
                GenerationRunModel.aspect_ratio == prompt.aspect_ratio,
            ))
            if existing is None:
                existing = GenerationRunModel(
                    tenant_id=run.tenant_id, pipeline_run_id=run.id, listing_task_id=run.listing_task_id, prompt_artifact_id=artifact.id,
                    provider=provider, model=model, aspect_ratio=prompt.aspect_ratio,
                    generation_number=cycle, status=GenerationRunStatus.PENDING.value,
                )
                session.add(existing)
                try:
                    session.flush()
                except IntegrityError:
                    existing = session.scalar(select(GenerationRunModel).where(
                        GenerationRunModel.tenant_id == run.tenant_id,
                        GenerationRunModel.pipeline_run_id == run.id,
                GenerationRunModel.listing_task_id == run.listing_task_id,
                        GenerationRunModel.provider == provider,
                        GenerationRunModel.model == model,
                        GenerationRunModel.aspect_ratio == prompt.aspect_ratio,
                    ))
            output.append((existing, prompt, artifact))
        return output

    async def execute(self):
        context = self.context
        if context.is_cancelled or context.shutdown_requested.is_set():
            return JobHandlerResult.cancelled()
        registry = self._registry()
        with context.dependencies.session_factory() as session:
            orch = CreativePipelineOrchestrator(session)
            try:
                node = orch.begin_node_execution(context.job.tenant_id, context.job.entity_id, context.job.id, context.job.lease_owner)
                run = session.scalar(select(PipelineRunModel).where(PipelineRunModel.tenant_id == context.job.tenant_id, PipelineRunModel.id == node.pipeline_run_id))
                listing = session.scalar(select(ListingTaskModel).where(ListingTaskModel.tenant_id == run.tenant_id, ListingTaskModel.id == run.listing_task_id)) if run else None
                if run is None or listing is None:
                    return JobHandlerResult.non_retryable("pipeline_lineage_unavailable", "Pipeline lineage is unavailable.")
                factory = context.dependencies.resources.get("creative_pipeline_storage_factory")
                if factory is None:
                    return JobHandlerResult.non_retryable("pipeline_storage_write_unsupported", "Pipeline storage is unavailable.")
                source = session.scalar(select(SourceGroupModel).where(SourceGroupModel.tenant_id == listing.tenant_id, SourceGroupModel.id == listing.source_group_id))
                if source is None:
                    return JobHandlerResult.non_retryable("pipeline_lineage_unavailable", "Pipeline source group is unavailable.")
                gateway = factory(run.tenant_id, source.external_source_id)
                rows = await self._prompt_rows(session, run, listing, gateway)
                generation_runs = self._ensure_runs(session, run, rows)
                session.commit()
                if registry is None:
                    return self._defer("creative_video_provider_not_configured", "Video provider registry is not configured.", 600)
                if any(self._registry().get(provider) is None for provider, *_ in generation_runs):
                    return self._defer("creative_video_provider_not_configured", "One or more video providers are not configured.", 600)
                unfinished = False
                for generation_run, prompt, artifact in generation_runs:
                    provider = registry.require(generation_run.provider)
                    if context.is_cancelled or context.shutdown_requested.is_set():
                        if generation_run.provider_request_id and generation_run.status in {GenerationRunStatus.SUBMITTED.value, GenerationRunStatus.RUNNING.value, GenerationRunStatus.RETRY_WAIT.value}:
                            try:
                                status = await _maybe(provider.cancel(VideoGenerationCancelInput(run.tenant_id, generation_run.provider_request_id)))
                                if getattr(status, "state", None) in CANONICAL_STATES:
                                    transition_generation_run(generation_run, GenerationRunStatus.CANCELLED.value)
                            except Exception:
                                pass
                        elif generation_run.status != GenerationRunStatus.COMPLETED.value:
                            transition_generation_run(generation_run, GenerationRunStatus.CANCELLED.value)
                        session.commit()
                        continue
                    if generation_run.status == GenerationRunStatus.COMPLETED.value:
                        continue
                    if generation_run.provider_request_id:
                        status = await _maybe(provider.poll(VideoGenerationPollInput(run.tenant_id, generation_run.provider_request_id)))
                    else:
                        request = VideoGenerationSubmitInput(run.tenant_id, generation_run.id, generation_run.provider, generation_run.model, generation_run.aspect_ratio, prompt.prompt, prompt.generation_parameters, f"creative_pipeline:generation:{generation_run.id}", ())
                        generation_run.attempt_count += 1
                        session.commit()
                        try:
                            submission = await _maybe(provider.submit(request))
                        except VideoGenerationProviderError as exc:
                            generation_run = session.get(GenerationRunModel, generation_run.id)
                            if exc.code == "submission_unknown":
                                return self._defer("creative_generation_submission_recovery_required", str(exc), 120)
                            if exc.retryable:
                                transition_generation_run(generation_run, GenerationRunStatus.RETRY_WAIT.value)
                                generation_run.last_error_code, generation_run.last_error_message = exc.code, str(exc)
                                session.commit()
                                return JobHandlerResult.retryable(exc.code, str(exc))
                            transition_generation_run(generation_run, GenerationRunStatus.FAILED.value)
                            generation_run.last_error_code, generation_run.last_error_message = exc.code, str(exc)
                            session.commit()
                            return JobHandlerResult.non_retryable(exc.code, str(exc))
                        generation_run = session.get(GenerationRunModel, generation_run.id)
                        generation_run.provider_request_id = submission.provider_request_id
                        if submission.state not in CANONICAL_STATES:
                            raise VideoGenerationProviderError("Unknown provider state.", code="provider_state_unknown", retryable=True)
                        status = VideoGenerationStatus(submission.state, provider_metadata=submission.provider_metadata)
                    if status.state == "completed":
                        transition_generation_run(generation_run, GenerationRunStatus.COMPLETED.value)
                        generation_run.completed_at = _now()
                    elif status.state in {"submitted", "running"}:
                        transition_generation_run(generation_run, status.state)
                        unfinished = True
                    elif status.state == "cancelled":
                        transition_generation_run(generation_run, GenerationRunStatus.CANCELLED.value)
                    elif status.state == "failed":
                        transition_generation_run(generation_run, GenerationRunStatus.FAILED.value)
                        generation_run.last_error_code = status.error_code or "provider_generation_failed"
                        generation_run.last_error_message = status.error_message or "Video generation failed."
                        session.commit()
                        return JobHandlerResult.non_retryable(generation_run.last_error_code, generation_run.last_error_message)
                    else:
                        raise VideoGenerationProviderError("Unknown provider state.", code="provider_state_unknown", retryable=True)
                    session.commit()
                if context.is_cancelled or context.shutdown_requested.is_set():
                    return JobHandlerResult.cancelled()
                if any(item.status in {GenerationRunStatus.PENDING.value, GenerationRunStatus.SUBMITTED.value, GenerationRunStatus.RUNNING.value, GenerationRunStatus.RETRY_WAIT.value} for item, *_ in generation_runs):
                    return self._defer("creative_video_generation_in_progress", "Video generation is still in progress.", 30)
                if any(item.status == GenerationRunStatus.FAILED.value for item, *_ in generation_runs):
                    return JobHandlerResult.non_retryable("creative_video_generation_failed", "A video generation branch failed.")
                if all(item.status == GenerationRunStatus.COMPLETED.value for item, *_ in generation_runs):
                    orch.complete_node(run.tenant_id, node.id, context.job.id, context.job.lease_owner, output_version=f"generation_{generation_runs[0][0].generation_number:03d}")
                    session.commit()
                    return JobHandlerResult.completed()
                return self._defer("creative_video_generation_waiting", "Video generation is waiting.", 30)
            except VideoGenerationProviderError as exc:
                session.rollback()
                if exc.code in {"creative_video_provider_not_configured", "creative_video_generation_config_missing", "creative_generation_submission_recovery_required"}:
                    return self._defer(exc.code, str(exc), exc.retry_after_seconds or 600)
                return JobHandlerResult.non_retryable(exc.code, str(exc)) if not exc.retryable else JobHandlerResult.retryable(exc.code, str(exc))
            except Exception:
                session.rollback()
                return JobHandlerResult.retryable("creative_video_generation_failed", "Video generation execution failed.")


class VideoGenerationNodeHandler:
    def __call__(self, context):
        return _run_async(CreativeVideoGenerationExecutor(context).execute())


def _run_async(coro):
    import asyncio
    return asyncio.run(coro)
