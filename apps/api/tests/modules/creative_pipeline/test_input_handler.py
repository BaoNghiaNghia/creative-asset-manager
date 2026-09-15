import asyncio
import logging
from threading import Event

from sqlalchemy import select

from app.domain.processing.handlers import ClaimedJob, JobHandlerContext, WorkerDependencies
from app.modules.creative_pipeline.input_handler import CreativePipelineNodeHandler
from app.modules.creative_pipeline.model import ArtifactModel, ListingTaskModel, NodeRunModel, PipelineRunModel
from app.modules.creative_pipeline.orchestrator import CreativePipelineOrchestrator
from app.modules.creative_pipeline.storage import StorageItem
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from tests.modules.creative_pipeline.test_storage import FakeStorage, make_listing
from tests.modules.creative_pipeline.test_orchestrator import setup


def context_for(sessions, job, node_type, storage):
    deps=WorkerDependencies(session_factory=sessions, resources={"creative_pipeline_storage_factory": lambda *_: storage})
    claimed=ClaimedJob(id=job.id, tenant_id="tenant-a", job_type=job.job_type, entity_type=job.entity_type, entity_id=job.entity_id, payload={"pipeline_run_id": job.payload_json["pipeline_run_id"], "node_run_id": job.entity_id, "node_type": node_type}, attempt_count=1, lease_owner="worker")
    return JobHandlerContext(claimed, deps, Event(), Event(), logging.LoggerAdapter(logging.getLogger("test"), {}))


def test_input_handler_materializes_snapshot_manifest_and_defers_future_nodes():
    engine, sessions = setup()
    try:
        with sessions.begin() as session:
            listing = session.get(ListingTaskModel, make_listing(sessions))
            run = PipelineRunModel(id="handler-run", tenant_id="tenant-a", listing_task_id=listing.id, run_number=1, trigger_type="discovery")
            session.add(run); session.flush()
            orch=CreativePipelineOrchestrator(session); orch.initialize_run("tenant-a", run.id)
            node=session.scalar(select(NodeRunModel).where(NodeRunModel.node_type=="input_data"))
            job=orch.schedule_node("tenant-a", node.id)
        with sessions.begin() as session:
            ProcessingRepository(session).claim_next_job(worker_id="worker", lease_seconds=300)
        storage=FakeStorage([
            StorageItem("listing-folder", "listing - 4527798886", None, "folder"),
            StorageItem("source-folder", "Source", "listing-folder", "folder"),
            StorageItem("ugc-folder", "UGC - Macro Vid", "listing-folder", "folder"),
            StorageItem("src-file", "photo.jpg", "source-folder", "image"),
            StorageItem("ugc-file", "clip.mp4", "ugc-folder", "video"),
        ])
        result=CreativePipelineNodeHandler() (context_for(sessions, job, "input_data", storage))
        assert result.outcome.value == "completed"
        with sessions() as session:
            assert session.scalar(select(NodeRunModel).where(NodeRunModel.node_type=="input_data")).status == "completed"
            run_row = session.scalar(select(PipelineRunModel).where(PipelineRunModel.id == "handler-run"))
            assert run_row.knowledge_snapshot_id and run_row.knowledge_snapshot_id.startswith("sha256:")
            knowledge = session.scalar(select(ArtifactModel).where(ArtifactModel.pipeline_run_id == "handler-run", ArtifactModel.artifact_type == "knowledge_snapshot"))
            assert knowledge.status == "available" and knowledge.relative_path == "Pipeline/Input/knowledge_snapshot_v001.json"
            assert len(session.scalars(select(ProcessingJobModel)).all()) == 2
        deferred=CreativePipelineNodeHandler()(context_for(sessions, job, "idea_story", storage))
        assert deferred.outcome.value == "non_retryable_failure"
        assert deferred.error_code == "ai_provider_unavailable"
    finally:
        engine.dispose()
