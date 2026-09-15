from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.creative_pipeline.model import (
    ArtifactModel, GenerationRunModel, ListingTaskModel, NodeRunModel,
    PipelineRunModel, SourceGroupModel,
)


class CreativePipelineRepository:
    """Tenant-scoped CP-01 persistence primitives.

    Methods flush but never commit. Callers own the transaction boundary.
    """

    def __init__(self, session: Session):
        self.session = session

    def create_source_group(self, **values) -> SourceGroupModel:
        row = SourceGroupModel(**values)
        self.session.add(row)
        self.session.flush()
        return row

    def get_source_group(self, tenant_id: str, group_id: str) -> SourceGroupModel | None:
        return self.session.scalar(select(SourceGroupModel).where(
            SourceGroupModel.tenant_id == tenant_id,
            SourceGroupModel.id == group_id,
        ))

    def get_source_group_by_identity(self, tenant_id: str, external_source_id: str, external_folder_id: str) -> SourceGroupModel | None:
        return self.session.scalar(select(SourceGroupModel).where(
            SourceGroupModel.tenant_id == tenant_id,
            SourceGroupModel.external_source_id == external_source_id,
            SourceGroupModel.external_folder_id == external_folder_id,
        ))

    def list_source_groups(self, tenant_id: str) -> list[SourceGroupModel]:
        return list(self.session.scalars(select(SourceGroupModel).where(SourceGroupModel.tenant_id == tenant_id).order_by(SourceGroupModel.name)))

    def create_listing_task(self, **values) -> ListingTaskModel:
        row = ListingTaskModel(**values)
        self.session.add(row)
        self.session.flush()
        return row

    def get_listing_task(self, tenant_id: str, listing_id: str) -> ListingTaskModel | None:
        return self.session.scalar(select(ListingTaskModel).where(
            ListingTaskModel.tenant_id == tenant_id,
            ListingTaskModel.id == listing_id,
        ))

    def get_listing_task_by_folder(self, tenant_id: str, source_group_id: str, external_folder_id: str) -> ListingTaskModel | None:
        return self.session.scalar(select(ListingTaskModel).where(
            ListingTaskModel.tenant_id == tenant_id,
            ListingTaskModel.source_group_id == source_group_id,
            ListingTaskModel.external_folder_id == external_folder_id,
        ))

    def list_listing_tasks(self, tenant_id: str, source_group_id: str | None = None) -> list[ListingTaskModel]:
        statement = select(ListingTaskModel).where(ListingTaskModel.tenant_id == tenant_id)
        if source_group_id is not None:
            statement = statement.where(ListingTaskModel.source_group_id == source_group_id)
        return list(self.session.scalars(statement.order_by(ListingTaskModel.folder_name)))

    def create_pipeline_run(self, **values) -> PipelineRunModel:
        row = PipelineRunModel(**values)
        self.session.add(row)
        self.session.flush()
        return row

    def get_pipeline_run(self, tenant_id: str, run_id: str) -> PipelineRunModel | None:
        return self.session.scalar(select(PipelineRunModel).where(
            PipelineRunModel.tenant_id == tenant_id,
            PipelineRunModel.id == run_id,
        ))

    def list_pipeline_runs(self, tenant_id: str, listing_task_id: str) -> list[PipelineRunModel]:
        return list(self.session.scalars(select(PipelineRunModel).where(
            PipelineRunModel.tenant_id == tenant_id,
            PipelineRunModel.listing_task_id == listing_task_id,
        ).order_by(PipelineRunModel.run_number)))

    def create_node_run(self, **values) -> NodeRunModel:
        row = NodeRunModel(**values)
        self.session.add(row)
        self.session.flush()
        return row

    def create_generation_run(self, **values) -> GenerationRunModel:
        row = GenerationRunModel(**values)
        self.session.add(row)
        self.session.flush()
        return row

    def create_artifact(self, **values) -> ArtifactModel:
        row = ArtifactModel(**values)
        self.session.add(row)
        self.session.flush()
        return row

    def list_artifacts(self, tenant_id: str, pipeline_run_id: str) -> list[ArtifactModel]:
        return list(self.session.scalars(select(ArtifactModel).where(
            ArtifactModel.tenant_id == tenant_id,
            ArtifactModel.pipeline_run_id == pipeline_run_id,
        ).order_by(ArtifactModel.created_at, ArtifactModel.version)))