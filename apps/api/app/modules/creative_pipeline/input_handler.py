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
from app.modules.creative_pipeline.knowledge import KnowledgeLoader, KnowledgePackError, KnowledgeStage
from app.modules.creative_pipeline.storage import PipelineStorageError, PipelineStorageUnsupported, ensure_pipeline_structure
from app.domain.providers.contracts import AiProviderError, AiStructuredTextInput
from app.modules.creative_pipeline.platforms import platform_profile
from app.modules.creative_pipeline.idea_story import (IdeaStory, IDEA_STORY_PROMPT_TEMPLATE_VERSION, IDEA_STORY_SCHEMA_NAME, assemble_idea_story_prompt, idea_story_json_schema, prompt_sha256)


class CreativePipelineNodeHandler:
    """Executes input_data and idea_story; later creative nodes remain deferred without attempts."""
    def __init__(self, settings=None):
        self.settings = settings

    def __call__(self, context: JobHandlerContext):
        node_type = str(context.job.payload.get("node_type") or "")
        if node_type == NodeType.IDEA_STORY.value:
            return asyncio.run(self._execute_idea_story(context))
        if node_type == NodeType.PROMPT.value:
            return self._prompt_dispatch(context)
        if node_type == NodeType.VIDEO_GENERATION.value:
            from app.modules.creative_pipeline.video_generation import VideoGenerationNodeHandler
            return VideoGenerationNodeHandler()(context)
        if node_type == NodeType.VIDEO_OUTPUT.value:
            from app.modules.creative_pipeline.video_output import VideoOutputNodeHandler
            return VideoOutputNodeHandler()(context)
        if node_type == NodeType.WATERMARK_SMART_ENHANCE.value:
            from app.modules.creative_pipeline.watermark_enhance import WatermarkSmartEnhanceNodeHandler
            return WatermarkSmartEnhanceNodeHandler()(context)
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
                listing = session.scalar(select(ListingTaskModel).where(ListingTaskModel.tenant_id == context.job.tenant_id, ListingTaskModel.id == run.listing_task_id)) if run else None
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
        ratios = list(platform_profile(listing.platform).required_aspect_ratios)
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
        # Freeze the exact versioned Knowledge Pack before input_data can complete.
        loader = KnowledgeLoader()
        knowledge_artifact = artifacts.reserve_artifact(
            tenant_id=listing.tenant_id, pipeline_run_id=run.id, node_run_id=node.id,
            artifact_type=ArtifactType.KNOWLEDGE_SNAPSHOT, version=1,
        )
        knowledge_artifact._artifact_parent_id = input_folder.id
        try:
            if knowledge_artifact.status == "available" and knowledge_artifact.external_file_id:
                downloader = getattr(gateway, "download_bytes", None)
                if downloader is None:
                    raise KnowledgePackError("knowledge_snapshot_download_unavailable")
                raw = downloader(knowledge_artifact.external_file_id)
                if hasattr(raw, "__await__"):
                    raw = await raw
                import json
                durable = loader.verify_snapshot(json.loads(raw.decode("utf-8")))
                if run.knowledge_snapshot_id != durable.snapshot_id:
                    if run.knowledge_snapshot_id:
                        raise KnowledgePackError("knowledge_snapshot_artifact_mismatch")
                    run.knowledge_snapshot_id = durable.snapshot_id
            else:
                snapshot = loader.create_run_snapshot(listing.platform)
                await artifacts.materialize_json(knowledge_artifact, gateway, snapshot.as_payload())
                if knowledge_artifact.status != "available":
                    raise KnowledgePackError("knowledge_snapshot_not_available")
                run.knowledge_snapshot_id = snapshot.snapshot_id
        except KnowledgePackError as exc:
            raise PipelineStorageError(str(exc)) from exc
        orch.complete_node(listing.tenant_id, node.id, context.job.id, context.job.lease_owner, output_version="v001")
        session.commit()
        return JobHandlerResult.completed()

    async def _execute_idea_story(self, context):
        if context.is_cancelled or context.shutdown_requested.is_set():
            return JobHandlerResult.cancelled()
        registry = context.dependencies.ai_provider_registry
        if registry is None:
            return JobHandlerResult.non_retryable("ai_provider_unavailable", "Structured AI provider is unavailable.")
        with context.dependencies.session_factory() as session:
            orch = CreativePipelineOrchestrator(session)
            try:
                node = orch.begin_node_execution(context.job.tenant_id, context.job.entity_id, context.job.id, context.job.lease_owner)
                run = session.scalar(select(PipelineRunModel).where(PipelineRunModel.tenant_id == context.job.tenant_id, PipelineRunModel.id == node.pipeline_run_id))
                listing = session.scalar(select(ListingTaskModel).where(ListingTaskModel.tenant_id == context.job.tenant_id, ListingTaskModel.id == run.listing_task_id)) if run else None
                group = session.scalar(select(SourceGroupModel).where(SourceGroupModel.tenant_id == context.job.tenant_id, SourceGroupModel.id == listing.source_group_id)) if listing else None
                source = session.scalar(select(ExternalSourceModel).where(ExternalSourceModel.tenant_id == context.job.tenant_id, ExternalSourceModel.id == group.external_source_id)) if group else None
                factory = context.dependencies.resources.get("creative_pipeline_storage_factory")
                if run is None or listing is None or source is None or factory is None:
                    return JobHandlerResult.non_retryable("pipeline_lineage_unavailable", "Creative Pipeline lineage or storage is unavailable.")
                gateway = factory(context.job.tenant_id, source.id)
                return await self._generate_idea_story(session, orch, node, run, listing, gateway, registry, context)
            except AiProviderError as exc:
                session.rollback()
                return JobHandlerResult.retryable(exc.code, str(exc)) if exc.retryable else JobHandlerResult.non_retryable(exc.code, str(exc))
            except (PipelineStorageError, KnowledgePackError, ValueError) as exc:
                session.rollback()
                return JobHandlerResult.non_retryable("idea_story_input_inconsistent", str(exc))
            except (TimeoutError, ConnectionError) as exc:
                session.rollback()
                return JobHandlerResult.retryable("idea_story_transient", str(exc))
            except Exception:
                session.rollback()
                return JobHandlerResult.retryable("idea_story_failed", "Idea Story generation failed.")

    async def _read_json_artifact(self, artifact, gateway):
        if artifact is None or artifact.status != "available" or not artifact.external_file_id or not artifact.content_hash:
            raise PipelineStorageError("artifact_inconsistent")
        downloader = getattr(gateway, "download_bytes", None)
        if downloader is None:
            raise PipelineStorageError("artifact_download_unsupported")
        raw = downloader(artifact.external_file_id)
        if hasattr(raw, "__await__"):
            raw = await raw
        import hashlib, json
        if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != artifact.content_hash:
            raise PipelineStorageError("artifact_hash_mismatch")
        if artifact.size_bytes is not None and len(raw) != artifact.size_bytes:
            raise PipelineStorageError("artifact_size_mismatch")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PipelineStorageError("artifact_json_invalid") from exc

    async def _generate_idea_story(self, session, orch, node, run, listing, gateway, registry, context):
        from app.modules.creative_pipeline.model import ArtifactModel
        artifacts = ArtifactService(session)
        input_artifact = session.scalar(select(ArtifactModel).where(
            ArtifactModel.tenant_id == listing.tenant_id, ArtifactModel.pipeline_run_id == run.id,
            ArtifactModel.artifact_type == ArtifactType.INPUT_SNAPSHOT.value, ArtifactModel.version == 1,
            ArtifactModel.status == "available"))
        input_snapshot = await self._read_json_artifact(input_artifact, gateway)
        if not isinstance(input_snapshot, dict) or input_snapshot.get("platform") != listing.platform or input_snapshot.get("listing", {}).get("id") != listing.id:
            raise PipelineStorageError("input_snapshot_lineage_mismatch")
        loader = KnowledgeLoader()
        durable = await loader.load_run_snapshot(listing.tenant_id, run.id, session, gateway)
        if run.knowledge_snapshot_id != durable.snapshot_id:
            raise KnowledgePackError("knowledge_snapshot_id_mismatch")
        bundle = loader.bundle_for_stage_from_snapshot(durable, KnowledgeStage.IDEA_STORY)
        ratios = input_snapshot.get("required_aspect_ratios") or []
        prompt = assemble_idea_story_prompt(input_snapshot=input_snapshot, knowledge_text=bundle.combined_text, platform=listing.platform, required_aspect_ratios=ratios)
        prompt_hash = prompt_sha256(prompt)
        artifact = artifacts.reserve_artifact(tenant_id=listing.tenant_id, pipeline_run_id=run.id, node_run_id=node.id, artifact_type=ArtifactType.IDEA_STORY, version=1)
        idea_folder = next((item for item in await gateway.list_children(getattr(listing, "pipeline_folder_id", "") or "") if item.name == "Idea Story" and item.kind == "folder"), None)
        if idea_folder is None:
            pipeline = await ensure_pipeline_structure(listing, gateway)
            idea_folder = next(item for item in await gateway.list_children(pipeline.id) if item.name == "Idea Story" and item.kind == "folder")
        artifact._artifact_parent_id = idea_folder.id
        expected = {"input_snapshot_sha256": input_artifact.content_hash, "knowledge_snapshot_id": durable.snapshot_id, "prompt_template_version": IDEA_STORY_PROMPT_TEMPLATE_VERSION}
        if artifact.status == "available":
            document = await self._read_json_artifact(artifact, gateway)
            IdeaStory.validate_document(document)
            if any(artifact.metadata_json.get(key) != value for key, value in expected.items()):
                raise PipelineStorageError("idea_artifact_provenance_mismatch")
            orch.complete_node(listing.tenant_id, node.id, context.job.id, context.job.lease_owner, output_version="v001")
            session.commit()
            return JobHandlerResult.completed()
        staged = (artifact.metadata_json or {}).get("staged_document")
        repair_count = int((artifact.metadata_json or {}).get("repair_count", 0))
        provider = registry.require("openai")
        if staged is not None:
            document = IdeaStory.validate_document(staged).model_dump(mode="json")
            provider_name = (artifact.metadata_json or {}).get("provider", "openai")
            model = (artifact.metadata_json or {}).get("model")
            request_id = (artifact.metadata_json or {}).get("provider_request_id")
            usage = (artifact.metadata_json or {}).get("usage", {})
            request_key = (artifact.metadata_json or {}).get("request_idempotency_key", f"creative_pipeline:idea_story:{node.id}:v001")
        else:
            if context.is_cancelled or context.shutdown_requested.is_set():
                return JobHandlerResult.cancelled()
            request_key = f"creative_pipeline:idea_story:{node.id}:v001"
            result = await provider.generate_structured(AiStructuredTextInput(
                tenant_id=listing.tenant_id, prompt=prompt, json_schema=idea_story_json_schema(),
                schema_name=IDEA_STORY_SCHEMA_NAME, idempotency_key=request_key,
                preferred_model=getattr(provider, "default_model", None),
                is_cancelled=lambda: context.is_cancelled or context.shutdown_requested.is_set()))
            provider_name, model, request_id, usage = result.provider, result.model, result.provider_request_id, dict(result.usage)
            try:
                document = IdeaStory.validate_document(result.document).model_dump(mode="json")
            except Exception as first_error:
                if context.is_cancelled or context.shutdown_requested.is_set():
                    return JobHandlerResult.cancelled()
                repair_key = f"{request_key}:repair:1"
                repair_prompt = prompt + "\n\nThe previous output failed schema validation. Return a corrected object only. Validation error: " + str(first_error)[:500]
                repaired = await provider.generate_structured(AiStructuredTextInput(
                    tenant_id=listing.tenant_id, prompt=repair_prompt, json_schema=idea_story_json_schema(),
                    schema_name=IDEA_STORY_SCHEMA_NAME, idempotency_key=repair_key,
                    preferred_model=getattr(provider, "default_model", None),
                    is_cancelled=lambda: context.is_cancelled or context.shutdown_requested.is_set()))
                repair_count = 1
                provider_name, model, request_id, usage = repaired.provider, repaired.model, repaired.provider_request_id, dict(repaired.usage)
                try:
                    document = IdeaStory.validate_document(repaired.document).model_dump(mode="json")
                except Exception as second_error:
                    raise PipelineStorageError("idea_story_schema_invalid") from second_error
        provenance = {**expected, "idea_story_schema_version": 1, "provider": provider_name, "model": model, "provider_request_id": request_id, "request_idempotency_key": request_key, "request_prompt_sha256": prompt_hash, "usage": usage, "repair_count": repair_count, "staged_document": document}
        artifact.metadata_json = provenance
        session.commit()
        await artifacts.materialize_json(artifact, gateway, document)
        artifact.metadata_json = {key: value for key, value in provenance.items() if key != "staged_document"}
        artifact.model_provider = provider_name
        artifact.model_name = model
        session.flush()
        orch.complete_node(listing.tenant_id, node.id, context.job.id, context.job.lease_owner, output_version="v001")
        session.commit()
        return JobHandlerResult.completed()

    def _prompt_dispatch(self, context):
        settings = context.dependencies.settings
        resources = context.dependencies.resources
        seedance_model = resources.get("creative_pipeline_seedance_model") or getattr(settings, "CREATIVE_PIPELINE_SEEDANCE_MODEL", None)
        omni_model = resources.get("creative_pipeline_google_omni_model") or getattr(settings, "CREATIVE_PIPELINE_GOOGLE_OMNI_MODEL", None)
        if not seedance_model or not omni_model:
            return DeferredJobOutcome("creative_prompt_target_not_configured", "Prompt target configuration is unavailable.", datetime.now(timezone.utc) + timedelta(minutes=10))
        return asyncio.run(self._execute_prompt(context, str(seedance_model), str(omni_model)))

    async def _execute_prompt(self, context, seedance_model, omni_model):
        if context.is_cancelled or context.shutdown_requested.is_set():
            return JobHandlerResult.cancelled()
        registry = context.dependencies.ai_provider_registry
        if registry is None:
            return JobHandlerResult.non_retryable("ai_provider_unavailable", "Structured AI provider is unavailable.")
        with context.dependencies.session_factory() as session:
            orch = CreativePipelineOrchestrator(session)
            try:
                node = orch.begin_node_execution(context.job.tenant_id, context.job.entity_id, context.job.id, context.job.lease_owner)
                run = session.scalar(select(PipelineRunModel).where(PipelineRunModel.tenant_id == context.job.tenant_id, PipelineRunModel.id == node.pipeline_run_id))
                listing = session.scalar(select(ListingTaskModel).where(ListingTaskModel.tenant_id == context.job.tenant_id, ListingTaskModel.id == run.listing_task_id)) if run else None
                group = session.scalar(select(SourceGroupModel).where(SourceGroupModel.tenant_id == context.job.tenant_id, SourceGroupModel.id == listing.source_group_id)) if listing else None
                source = session.scalar(select(ExternalSourceModel).where(ExternalSourceModel.tenant_id == context.job.tenant_id, ExternalSourceModel.id == group.external_source_id)) if group else None
                factory = context.dependencies.resources.get("creative_pipeline_storage_factory")
                if not run or not listing or not source or not factory:
                    return JobHandlerResult.non_retryable("pipeline_lineage_unavailable", "Creative Pipeline lineage or storage is unavailable.")
                gateway = factory(context.job.tenant_id, source.id)
                from app.modules.creative_pipeline.model import ArtifactModel
                from app.modules.creative_pipeline.idea_story import IdeaStory, canonical_json as idea_json
                from app.modules.creative_pipeline.prompt_generation import (
                    SEEDANCE_PROMPT_TEMPLATE_VERSION, GOOGLE_OMNI_PROMPT_TEMPLATE_VERSION,
                    assemble_prompt, durable_prompt, draft_schema, prompt_hash, validate_draft, DurablePrompt,
                )
                profile = platform_profile(listing.platform)
                idea_artifact = session.scalar(select(ArtifactModel).where(ArtifactModel.tenant_id == listing.tenant_id, ArtifactModel.pipeline_run_id == run.id, ArtifactModel.artifact_type == ArtifactType.IDEA_STORY.value, ArtifactModel.version == 1, ArtifactModel.status == "available", ArtifactModel.variant_key.is_(None)))
                idea_doc = await self._read_json_artifact(idea_artifact, gateway)
                IdeaStory.validate_document(idea_doc)
                loader = KnowledgeLoader()
                durable = await loader.load_run_snapshot(listing.tenant_id, run.id, session, gateway)
                if durable.snapshot_id != run.knowledge_snapshot_id or (idea_artifact.metadata_json or {}).get("knowledge_snapshot_id") != durable.snapshot_id:
                    raise KnowledgePackError("knowledge_snapshot_id_mismatch")
                provider = registry.require("openai")
                pipeline = await ensure_pipeline_structure(listing, gateway)
                prompt_root = next(item for item in await gateway.list_children(pipeline.id) if item.name == "Prompt" and item.kind == "folder")
                targets = (("seedance", seedance_model, KnowledgeStage.SEEDANCE_2_5, SEEDANCE_PROMPT_TEMPLATE_VERSION), ("google_omni", omni_model, KnowledgeStage.GOOGLE_OMNI, GOOGLE_OMNI_PROMPT_TEMPLATE_VERSION))
                for target, model, stage, template in targets:
                    artifact = ArtifactService(session).reserve_artifact(tenant_id=listing.tenant_id, pipeline_run_id=run.id, node_run_id=node.id, artifact_type=ArtifactType.PROMPT, version=1, variant_key=target)
                    folder = next((x for x in await gateway.list_children(prompt_root.id) if x.name == target and x.kind == "folder"), None)
                    if folder is None:
                        folder = await gateway.create_folder(prompt_root.id, target)
                    artifact._artifact_parent_id = folder.id
                    expected = {"target_provider": target, "target_model": model, "knowledge_snapshot_id": durable.snapshot_id, "idea_story_artifact_id": idea_artifact.id, "idea_story_sha256": idea_artifact.content_hash, "prompt_template_version": template}
                    if artifact.status == "available":
                        existing = await self._read_json_artifact(artifact, gateway)
                        try:
                            durable_existing = DurablePrompt.model_validate(existing)
                            ratios = [item.aspect_ratio for item in durable_existing.outputs]
                            if (
                                durable_existing.platform != listing.platform
                                or ratios != list(profile.required_aspect_ratios)
                                or any(item.provider != target or item.model != model or item.knowledge_snapshot_id != durable.snapshot_id for item in durable_existing.outputs)
                                or (artifact.metadata_json or {}).get("idea_story_sha256") != idea_artifact.content_hash
                            ):
                                raise ValueError("prompt artifact provenance mismatch")
                        except Exception as exc:
                            raise PipelineStorageError("prompt_artifact_provenance_mismatch") from exc
                        continue
                    staged = (artifact.metadata_json or {}).get("staged_document")
                    repair_count = int((artifact.metadata_json or {}).get("repair_count", 0))
                    if staged is not None:
                        durable_doc = staged
                    else:
                        bundle = loader.bundle_for_stage_from_snapshot(durable, stage)
                        prompt = assemble_prompt(target_provider=target, target_model=model, template_version=template, idea_story=idea_doc, knowledge_text=bundle.combined_text, platform=profile)
                        result = await provider.generate_structured(AiStructuredTextInput(tenant_id=listing.tenant_id, prompt=prompt, json_schema=draft_schema(), schema_name=f"creative_pipeline_{target}_prompt_draft_v1", idempotency_key=f"creative_pipeline:prompt:{node.id}:{target}:v001", preferred_model=getattr(provider, "default_model", None), is_cancelled=lambda: context.is_cancelled or context.shutdown_requested.is_set()))
                        try:
                            draft = validate_draft(result.document, profile)
                        except Exception as first_error:
                            repair_prompt = prompt + "\nReturn a corrected draft only. " + str(first_error)[:400]
                            repair = await provider.generate_structured(
                                AiStructuredTextInput(
                                    tenant_id=listing.tenant_id,
                                    prompt=repair_prompt,
                                    json_schema=draft_schema(),
                                    schema_name=f"creative_pipeline_{target}_prompt_draft_v1",
                                    idempotency_key=f"creative_pipeline:prompt:{node.id}:{target}:v001:repair:1",
                                    preferred_model=getattr(provider, "default_model", None),
                                    is_cancelled=lambda: context.is_cancelled or context.shutdown_requested.is_set(),
                                )
                            )
                            repair_count = 1
                            try: draft = validate_draft(repair.document, profile)
                            except Exception as exc: raise PipelineStorageError("prompt_schema_invalid") from exc
                        durable_doc = durable_prompt(provider=target, model=model, platform=listing.platform, idea_story_version=1, snapshot_id=durable.snapshot_id, draft=draft)
                        provenance = {**expected, "authoring_provider": result.provider, "authoring_model": result.model, "provider_request_id": result.provider_request_id, "request_idempotency_key": f"creative_pipeline:prompt:{node.id}:{target}:v001", "request_prompt_sha256": prompt_hash(prompt), "repair_count": repair_count, "usage": dict(result.usage), "staged_document": durable_doc}
                        artifact.metadata_json = provenance
                        session.commit()
                    if staged is not None:
                        durable_doc = staged
                    await ArtifactService(session).materialize_json(artifact, gateway, durable_doc)
                    artifact.model_provider = target
                    artifact.model_name = model
                    artifact.metadata_json = {k:v for k,v in (artifact.metadata_json or {}).items() if k != "staged_document"}
                    session.flush()
                orch.complete_node(listing.tenant_id, node.id, context.job.id, context.job.lease_owner, output_version="v001")
                session.commit()
                return JobHandlerResult.completed()
            except AiProviderError as exc:
                session.rollback()
                return JobHandlerResult.retryable(exc.code, str(exc)) if exc.retryable else JobHandlerResult.non_retryable(exc.code, str(exc))
            except (PipelineStorageError, KnowledgePackError, ValueError) as exc:
                session.rollback()
                return JobHandlerResult.non_retryable("prompt_input_inconsistent", str(exc))
            except (TimeoutError, ConnectionError) as exc:
                session.rollback()
                return JobHandlerResult.retryable("prompt_transient", str(exc))
            except Exception:
                session.rollback()
                return JobHandlerResult.retryable("prompt_generation_failed", "Prompt generation failed.")
