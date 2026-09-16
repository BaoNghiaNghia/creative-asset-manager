from __future__ import annotations

import hashlib
import json
import inspect
import os
import tempfile
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.creative_pipeline.model import ArtifactModel, GenerationRunModel
from app.modules.creative_pipeline.storage import PipelineStorageError, CreativePipelineStorageGateway


class ArtifactService:
    def __init__(self, session: Session):
        self.session = session

    def reserve_artifact(self, *, tenant_id, pipeline_run_id, node_run_id, generation_run_id=None, artifact_type, version=1, aspect_ratio=None, variant_key=None, relative_path=None):
        value = artifact_type.value if hasattr(artifact_type, "value") else artifact_type
        if variant_key is not None and (not isinstance(variant_key, str) or not variant_key or variant_key != variant_key.lower() or "/" in variant_key or chr(92) in variant_key or not variant_key.replace("_", "").isalnum()):
            raise ValueError("invalid artifact variant_key")
        stmt = select(ArtifactModel).where(
            ArtifactModel.tenant_id == tenant_id,
            ArtifactModel.pipeline_run_id == pipeline_run_id,
            ArtifactModel.artifact_type == value,
            ArtifactModel.version == version,
            ArtifactModel.variant_key == variant_key,
        )
        if aspect_ratio is None:
            stmt = stmt.where(ArtifactModel.aspect_ratio.is_(None))
        else:
            stmt = stmt.where(ArtifactModel.aspect_ratio == aspect_ratio)
        existing = self.session.scalar(stmt)
        if existing:
            if generation_run_id is not None and existing.generation_run_id != generation_run_id:
                raise PipelineStorageError("artifact_generation_lineage_mismatch")
            if existing.node_run_id != node_run_id or (aspect_ratio is not None and existing.aspect_ratio != aspect_ratio) or (variant_key is not None and existing.variant_key != variant_key) or (relative_path is not None and existing.relative_path != relative_path):
                raise PipelineStorageError("artifact_identity_mismatch")
            return existing
        try:
            with self.session.begin_nested():
                row = ArtifactModel(
                    tenant_id=tenant_id, listing_task_id=self._listing_id(tenant_id, pipeline_run_id),
                    pipeline_run_id=pipeline_run_id, node_run_id=node_run_id, generation_run_id=generation_run_id,
                    artifact_type=value, version=version, variant_key=variant_key,
                    relative_path=relative_path or self._default_path(value, version, aspect_ratio, variant_key),
                    status="reserved", metadata_json={},
                    storage_kind="source_provider",
                )
                self.session.add(row)
                self.session.flush()
            return row
        except IntegrityError:
            row = self.session.scalar(stmt)
            if row is None:
                raise PipelineStorageError("artifact_reservation_conflict")
            if generation_run_id is not None and row.generation_run_id != generation_run_id:
                raise PipelineStorageError("artifact_generation_lineage_mismatch")
            return row

    def reserve_next_artifact(self, *, tenant_id, pipeline_run_id, node_run_id, artifact_type, aspect_ratio=None, variant_key=None, generation_run_id=None, relative_path_factory=None):
        listing_id = self._listing_id(tenant_id, pipeline_run_id)
        value = artifact_type.value if hasattr(artifact_type, "value") else artifact_type
        existing_stmt = select(ArtifactModel).where(
            ArtifactModel.tenant_id == tenant_id, ArtifactModel.pipeline_run_id == pipeline_run_id,
            ArtifactModel.artifact_type == value, ArtifactModel.listing_task_id == listing_id,
            ArtifactModel.aspect_ratio == aspect_ratio, ArtifactModel.variant_key == variant_key,
        )
        existing = self.session.scalar(existing_stmt)
        if existing is not None:
            return existing
        for _ in range(3):
            max_stmt = select(func.max(ArtifactModel.version)).where(
                ArtifactModel.tenant_id == tenant_id, ArtifactModel.listing_task_id == listing_id,
                ArtifactModel.artifact_type == value, ArtifactModel.aspect_ratio == aspect_ratio,
                ArtifactModel.variant_key == variant_key,
            )
            version = int(self.session.scalar(max_stmt) or 0) + 1
            path = relative_path_factory(version) if relative_path_factory else None
            try:
                return self.reserve_artifact(tenant_id=tenant_id, pipeline_run_id=pipeline_run_id, node_run_id=node_run_id, generation_run_id=generation_run_id, artifact_type=value, version=version, aspect_ratio=aspect_ratio, variant_key=variant_key, relative_path=path)
            except IntegrityError:
                self.session.rollback()
                existing = self.session.scalar(existing_stmt)
                if existing is not None: return existing
        raise PipelineStorageError("artifact_version_allocation_conflict")

    def _listing_id(self, tenant_id, run_id):
        from app.modules.creative_pipeline.model import PipelineRunModel
        run = self.session.scalar(select(PipelineRunModel).where(PipelineRunModel.tenant_id == tenant_id, PipelineRunModel.id == run_id))
        if run is None:
            raise PipelineStorageError("pipeline_run_unavailable")
        return run.listing_task_id

    @staticmethod
    def _default_path(value, version, aspect_ratio, variant_key=None):
        if value == "prompt" and variant_key:
            return f"Pipeline/Prompt/{variant_key}/prompt_v{version:03d}.json"
        names = {"input_snapshot": f"input_v{version:03d}.json", "input_manifest": f"input_manifest_v{version:03d}.json", "knowledge_snapshot": f"knowledge_snapshot_v{version:03d}.json", "idea_story": f"Idea Story/idea_v{version:03d}.json"}
        name = names.get(value, value + f"_v{version:03d}.json")
        if value == "idea_story":
            return f"Pipeline/{name}"
        return f"Pipeline/Input/{name}"

    def canonical_json(self, payload):
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    async def materialize_json(self, artifact: ArtifactModel, gateway: CreativePipelineStorageGateway, payload: dict):
        content = self.canonical_json(payload)
        digest = hashlib.sha256(content).hexdigest()
        if artifact.status == "available" and artifact.external_file_id:
            item = await gateway.get_item(artifact.external_file_id)
            if item is not None:
                return artifact
            artifact.status = "inconsistent"
        final_name = artifact.relative_path.rsplit("/", 1)[-1]
        parent_path = artifact.relative_path.rsplit("/", 1)[0]
        # Resolve the known Pipeline tree rather than accepting arbitrary parent IDs.
        pipeline = await gateway.get_item(getattr(artifact, "_pipeline_folder_id", "")) if getattr(artifact, "_pipeline_folder_id", None) else None
        if artifact.external_file_id is None:
            parent_id = getattr(artifact, "_artifact_parent_id", None)
            if not parent_id:
                raise PipelineStorageError("artifact_parent_unresolved")
            existing = [item for item in await gateway.list_children(parent_id) if item.name == final_name]
            if existing:
                raise PipelineStorageError("artifact_name_collision")
            item = await gateway.upload_bytes(parent_id, f".__cp_tmp_{artifact.id}", "application/json", content)
            item = await gateway.rename_item(item.id, final_name)
            artifact.external_file_id = item.id
        artifact.content_hash = digest
        artifact.size_bytes = len(content)
        artifact.mime_type = "application/json"
        artifact.status = "available"
        artifact.available_at = datetime.now(timezone.utc)
        artifact.relative_path = f"{parent_path}/{final_name}"
        return artifact

    async def materialize_video(self, artifact, gateway, result, *, max_output_bytes: int):
        if artifact.status == "available" and artifact.external_file_id:
            if await gateway.get_item(artifact.external_file_id) is not None:
                return artifact
            artifact.status = "inconsistent"
        if result.content_type != "video/mp4":
            raise PipelineStorageError("creative_video_output_unsupported")
        if result.content is None:
            raise PipelineStorageError("creative_video_result_unavailable")
        digest = hashlib.sha256()
        total = 0
        first = bytearray()
        fd, temp_path = tempfile.mkstemp(prefix="cp-video-", suffix=".mp4")
        os.close(fd)
        try:
            with open(temp_path, "wb") as handle:
                async def write_chunk(chunk):
                    nonlocal total
                    if not isinstance(chunk, (bytes, bytearray, memoryview)):
                        raise PipelineStorageError("creative_video_output_unsupported")
                    data = bytes(chunk)
                    if not data:
                        return
                    total += len(data)
                    if total > max_output_bytes:
                        raise PipelineStorageError("creative_video_output_too_large")
                    if len(first) < 64:
                        first.extend(data[: 64 - len(first)])
                    digest.update(data)
                    handle.write(data)
                content = result.content
                if isinstance(content, (bytes, bytearray, memoryview)):
                    await write_chunk(content)
                elif hasattr(content, "__aiter__"):
                    async for chunk in content:
                        await write_chunk(chunk)
                elif isinstance(content, (str, os.PathLike)):
                    with open(content, "rb") as source:
                        while True:
                            chunk = source.read(1024 * 1024)
                            if not chunk:
                                break
                            await write_chunk(chunk)
                elif callable(content):
                    stream = content()
                    stream = await stream if inspect.isawaitable(stream) else stream
                    async for chunk in stream:
                        await write_chunk(chunk)
                else:
                    raise PipelineStorageError("creative_video_result_unavailable")
            if total <= 0 or b"ftyp" not in bytes(first):
                raise PipelineStorageError("creative_video_output_unsupported")
            computed = digest.hexdigest()
            if result.checksum and result.checksum.lower() != computed:
                raise PipelineStorageError("creative_video_output_checksum_mismatch")
            final_name = artifact.relative_path.rsplit("/", 1)[-1]
            parent_id = getattr(artifact, "_artifact_parent_id", None)
            if not parent_id:
                raise PipelineStorageError("artifact_parent_unresolved")
            existing = [item for item in await gateway.list_children(parent_id) if item.name == final_name]
            if existing and not (artifact.external_file_id and any(item.id == artifact.external_file_id for item in existing)):
                raise PipelineStorageError("artifact_name_collision")
            upload_file = getattr(gateway, "upload_file", None)
            if upload_file is not None:
                item = await upload_file(parent_id, f".__cp_tmp_{artifact.id}", "video/mp4", temp_path)
            else:
                # Explicit bounded compatibility fallback for legacy byte-only gateways.
                if total > max_output_bytes:
                    raise PipelineStorageError("creative_video_output_too_large")
                with open(temp_path, "rb") as source:
                    content = source.read()
                item = await gateway.upload_bytes(parent_id, f".__cp_tmp_{artifact.id}", "video/mp4", content)
            item = await gateway.rename_item(item.id, final_name)
            artifact.external_file_id = item.id
            artifact.content_hash = computed
            artifact.size_bytes = total
            artifact.mime_type = "video/mp4"
            artifact.status = "available"
            artifact.available_at = datetime.now(timezone.utc)
            return artifact
        finally:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
