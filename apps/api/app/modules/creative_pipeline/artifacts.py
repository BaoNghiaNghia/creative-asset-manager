from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.creative_pipeline.model import ArtifactModel
from app.modules.creative_pipeline.storage import PipelineStorageError, CreativePipelineStorageGateway


class ArtifactService:
    def __init__(self, session: Session):
        self.session = session

    def reserve_artifact(self, *, tenant_id, pipeline_run_id, node_run_id, artifact_type, version=1, aspect_ratio=None, relative_path=None):
        value = artifact_type.value if hasattr(artifact_type, "value") else artifact_type
        stmt = select(ArtifactModel).where(
            ArtifactModel.tenant_id == tenant_id,
            ArtifactModel.pipeline_run_id == pipeline_run_id,
            ArtifactModel.artifact_type == value,
            ArtifactModel.version == version,
        )
        if aspect_ratio is None:
            stmt = stmt.where(ArtifactModel.aspect_ratio.is_(None))
        else:
            stmt = stmt.where(ArtifactModel.aspect_ratio == aspect_ratio)
        existing = self.session.scalar(stmt)
        if existing:
            return existing
        try:
            with self.session.begin_nested():
                row = ArtifactModel(
                    tenant_id=tenant_id, listing_task_id=self._listing_id(tenant_id, pipeline_run_id),
                    pipeline_run_id=pipeline_run_id, node_run_id=node_run_id,
                    artifact_type=value, version=version,
                    relative_path=relative_path or self._default_path(value, version, aspect_ratio),
                    status="reserved", metadata_json={},
                    storage_kind="source_provider",
                )
                self.session.add(row)
                self.session.flush()
            return row
        except IntegrityError:
            return self.session.scalar(stmt)

    def _listing_id(self, tenant_id, run_id):
        from app.modules.creative_pipeline.model import PipelineRunModel
        run = self.session.scalar(select(PipelineRunModel).where(PipelineRunModel.tenant_id == tenant_id, PipelineRunModel.id == run_id))
        if run is None:
            raise PipelineStorageError("pipeline_run_unavailable")
        return run.listing_task_id

    @staticmethod
    def _default_path(value, version, aspect_ratio):
        names = {"input_snapshot": f"input_v{version:03d}.json", "input_manifest": f"input_manifest_v{version:03d}.json"}
        return f"Pipeline/Input/{names.get(value, value + f'_v{version:03d}.json')}"

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
