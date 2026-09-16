from __future__ import annotations
from sqlalchemy import select
from app.modules.creative_pipeline.model import ArtifactModel, GenerationRunModel, NodeRunModel, PipelineRunModel
from app.modules.creative_pipeline.constants import NodeType

class CreativePipelineLineageError(RuntimeError): pass

class CreativePipelineLineageResolver:
    def __init__(self, session, *, max_depth=32):
        self.session, self.max_depth = session, max_depth

    def ancestors(self, run):
        result, seen, current = [], set(), run
        while current is not None:
            if current.id in seen: raise CreativePipelineLineageError("pipeline_lineage_cycle")
            if len(result) >= self.max_depth: raise CreativePipelineLineageError("pipeline_lineage_depth_exceeded")
            seen.add(current.id); result.append(current)
            if current.parent_run_id is None: break
            current = self.session.scalar(select(PipelineRunModel).where(
                PipelineRunModel.tenant_id == run.tenant_id,
                PipelineRunModel.id == current.parent_run_id,
                PipelineRunModel.listing_task_id == run.listing_task_id,
            ))
            if current is None: raise CreativePipelineLineageError("pipeline_parent_missing")
        return result

    def effective_artifact(self, run, artifact_type, *, aspect_ratio=None, variant_key=None):
        for owner in self.ancestors(run):
            stmt=select(ArtifactModel).where(ArtifactModel.tenant_id==run.tenant_id, ArtifactModel.pipeline_run_id==owner.id, ArtifactModel.listing_task_id==run.listing_task_id, ArtifactModel.artifact_type==artifact_type, ArtifactModel.status=="available")
            if aspect_ratio is None: stmt=stmt.where(ArtifactModel.aspect_ratio.is_(None))
            else: stmt=stmt.where(ArtifactModel.aspect_ratio==aspect_ratio)
            if variant_key is None: stmt=stmt.where(ArtifactModel.variant_key.is_(None))
            else: stmt=stmt.where(ArtifactModel.variant_key==variant_key)
            row=self.session.scalar(stmt.order_by(ArtifactModel.version.desc()))
            if row is not None: return row
        return None

    def effective_node(self, run, node_type):
        for owner in self.ancestors(run):
            row=self.session.scalar(select(NodeRunModel).where(NodeRunModel.tenant_id==run.tenant_id,NodeRunModel.pipeline_run_id==owner.id,NodeRunModel.node_type==node_type))
            if row is not None: return row, owner.id != run.id
        return None, False

    def effective_input_snapshot(self, run): return self.effective_artifact(run, "input_snapshot")

    def effective_knowledge_artifact(self, run): return self.effective_artifact(run, "knowledge_snapshot")

    def effective_idea_story(self, run): return self.effective_artifact(run, "idea_story")

    def effective_prompt(self, run, provider): return self.effective_artifact(run, "prompt", variant_key=provider)

    def is_ancestor_run(self, candidate_run_id, child_run): return any(owner.id == candidate_run_id for owner in self.ancestors(child_run))

    def effective_knowledge_snapshot(self, run):
        for owner in self.ancestors(run):
            if owner.knowledge_snapshot_id: return owner.knowledge_snapshot_id, owner.id
        return None, None

    def effective_prompt_artifacts(self, run):
        return [self.effective_artifact(run, "prompt", variant_key=variant) for variant in ("seedance","google_omni")]
