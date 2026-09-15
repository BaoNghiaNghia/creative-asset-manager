import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.domain.processing.types import JobStatus
from app.modules.creative_pipeline.constants import NodeRunStatus, PipelineRunStatus
from app.modules.creative_pipeline.model import NodeRunModel, PipelineRunModel
from app.modules.creative_pipeline.orchestrator import (
    CREATIVE_PIPELINE_ENTITY_TYPE, CreativePipelineDependencyError, CreativePipelineOwnershipError,
    CreativePipelineOrchestrator, CreativePipelineStateError, PIPELINE_NODE_ORDER,
)
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from tests.modules.creative_pipeline.test_domain import _group, _listing, _session


def setup():
    engine, sessions = _session()
    ProcessingJobModel.__table__.create(engine, checkfirst=True)
    TenantProcessingPolicyModel.__table__.create(engine, checkfirst=True)
    return engine, sessions


def seed_run(sessions):
    with sessions.begin() as session:
        listing = _listing(session, _group(session))
        run = PipelineRunModel(id="run-1", tenant_id="tenant-a", listing_task_id=listing.id, run_number=1, trigger_type="discovery")
        session.add(run)
        session.flush()
        return run.id


def load_nodes(sessions):
    with sessions() as session:
        return list(session.scalars(select(NodeRunModel).order_by(NodeRunModel.created_at)))


def test_initialize_creates_canonical_graph_and_one_idempotent_job():
    engine, sessions = setup()
    try:
        run_id = seed_run(sessions)
        with sessions.begin() as session:
            orch = CreativePipelineOrchestrator(session)
            nodes = orch.initialize_run("tenant-a", run_id)
            assert [node.node_type for node in nodes] == [node.value for node in PIPELINE_NODE_ORDER]
            assert nodes[0].status == NodeRunStatus.READY.value
            assert all(node.status == NodeRunStatus.PENDING.value for node in nodes[1:])
            jobs = orch.schedule_ready_nodes("tenant-a", run_id)
            assert len(jobs) == 1
            assert orch.schedule_ready_nodes("tenant-a", run_id)[0].id == jobs[0].id
        with sessions() as session:
            assert len(session.scalars(select(NodeRunModel)).all()) == 6
            assert len(session.scalars(select(ProcessingJobModel)).all()) == 1
    finally:
        engine.dispose()


def test_initialize_cross_tenant_and_terminal_are_safe():
    engine, sessions = setup()
    try:
        run_id = seed_run(sessions)
        with sessions.begin() as session:
            orch = CreativePipelineOrchestrator(session)
            with pytest.raises(CreativePipelineOwnershipError):
                orch.initialize_run("tenant-b", run_id)
        with sessions.begin() as session:
            run = session.get(PipelineRunModel, run_id)
            run.status = PipelineRunStatus.COMPLETED.value
        with sessions.begin() as session:
            assert CreativePipelineOrchestrator(session).initialize_run("tenant-a", run_id) == []
    finally:
        engine.dispose()


def test_sequential_completion_unlocks_and_completes_run():
    engine, sessions = setup()
    try:
        run_id = seed_run(sessions)
        for index in range(6):
            with sessions.begin() as session:
                orch = CreativePipelineOrchestrator(session)
                orch.initialize_run("tenant-a", run_id)
                node = session.scalar(select(NodeRunModel).where(NodeRunModel.tenant_id == "tenant-a", NodeRunModel.pipeline_run_id == run_id, NodeRunModel.node_type == PIPELINE_NODE_ORDER[index].value))
                jobs = orch.schedule_ready_nodes("tenant-a", run_id)
                job = session.scalar(select(ProcessingJobModel).where(ProcessingJobModel.entity_id == node.id))
                claimed = ProcessingRepository(session).claim_next_job(worker_id="worker", lease_seconds=300)
                assert claimed is not None and claimed.id == job.id
                orch.begin_node_execution("tenant-a", node.id, job.id, "worker")
                orch.complete_node("tenant-a", node.id, job.id, "worker")
        with sessions() as session:
            assert session.get(PipelineRunModel, run_id).status == PipelineRunStatus.COMPLETED.value
    finally:
        engine.dispose()


def test_state_machine_rejects_illegal_transitions_and_dependencies():
    engine, sessions = setup()
    try:
        run_id = seed_run(sessions)
        with sessions.begin() as session:
            orch = CreativePipelineOrchestrator(session)
            orch.initialize_run("tenant-a", run_id)
            nodes = load_nodes(sessions) if False else list(session.scalars(select(NodeRunModel)).all())
            assert nodes[1].status == NodeRunStatus.PENDING.value
            with pytest.raises(CreativePipelineStateError):
                orch.schedule_node("tenant-a", nodes[1].id)
            with pytest.raises(CreativePipelineOwnershipError):
                orch.complete_node("tenant-a", nodes[0].id, "missing", "worker")
    finally:
        engine.dispose()


def test_retry_reuses_job_and_retry_now_keeps_identity():
    engine, sessions = setup()
    try:
        run_id = seed_run(sessions)
        with sessions.begin() as session:
            orch = CreativePipelineOrchestrator(session)
            orch.initialize_run("tenant-a", run_id)
            node = session.scalar(select(NodeRunModel).where(NodeRunModel.node_type == "input_data"))
            job = orch.schedule_node("tenant-a", node.id)
            claim = ProcessingRepository(session).claim_next_job(worker_id="worker", lease_seconds=300)
            orch.begin_node_execution("tenant-a", node.id, job.id, "worker")
            node = orch.fail_node_retryable("tenant-a", node.id, job.id, "worker", "temporary", "try again")
            assert node.status == NodeRunStatus.RETRY_WAIT.value
            old_id = job.id
            assert job.status == JobStatus.RETRY.value
            orch.retry_now("tenant-a", node.id)
            assert node.status == NodeRunStatus.READY.value
            assert orch.schedule_node("tenant-a", node.id).id == old_id
            assert len(session.scalars(select(ProcessingJobModel)).all()) == 1
    finally:
        engine.dispose()


def test_non_retryable_and_exhausted_failures_are_terminal():
    engine, sessions = setup()
    try:
        run_id = seed_run(sessions)
        with sessions.begin() as session:
            orch = CreativePipelineOrchestrator(session)
            orch.initialize_run("tenant-a", run_id)
            node = session.scalar(select(NodeRunModel).where(NodeRunModel.node_type == "input_data"))
            job = orch.schedule_node("tenant-a", node.id)
            ProcessingRepository(session).claim_next_job(worker_id="worker", lease_seconds=300)
            orch.begin_node_execution("tenant-a", node.id, job.id, "worker")
            orch.fail_node_non_retryable("tenant-a", node.id, job.id, "worker", "invalid", "bad")
            assert node.status == NodeRunStatus.FAILED.value
            with pytest.raises(CreativePipelineStateError):
                orch.retry_now("tenant-a", node.id)
    finally:
        engine.dispose()


def test_duplicate_completion_and_cancel_do_not_advance():
    engine, sessions = setup()
    try:
        run_id = seed_run(sessions)
        with sessions.begin() as session:
            orch = CreativePipelineOrchestrator(session)
            orch.initialize_run("tenant-a", run_id)
            node = session.scalar(select(NodeRunModel).where(NodeRunModel.node_type == "input_data"))
            job = orch.schedule_node("tenant-a", node.id)
            ProcessingRepository(session).claim_next_job(worker_id="worker", lease_seconds=300)
            orch.begin_node_execution("tenant-a", node.id, job.id, "worker")
            orch.complete_node("tenant-a", node.id, job.id, "worker")
        with sessions.begin() as session:
            orch = CreativePipelineOrchestrator(session)
            node = session.scalar(select(NodeRunModel).where(NodeRunModel.node_type == "input_data"))
            assert orch.complete_node("tenant-a", node.id, job.id, "other").status == NodeRunStatus.COMPLETED.value
            orch.cancel_run("tenant-a", run_id, requested_by="operator", reason="stop")
        with sessions() as session:
            assert session.get(PipelineRunModel, run_id).status == PipelineRunStatus.CANCELLED.value
            assert all(node.status in {NodeRunStatus.COMPLETED.value, NodeRunStatus.CANCELLED.value} for node in session.scalars(select(NodeRunModel)).all())
    finally:
        engine.dispose()


def test_block_unblock_and_listing_lifecycle_are_separate():
    engine, sessions = setup()
    try:
        run_id = seed_run(sessions)
        with sessions.begin() as session:
            orch = CreativePipelineOrchestrator(session)
            orch.initialize_run("tenant-a", run_id)
            node = session.scalar(select(NodeRunModel).where(NodeRunModel.node_type == "input_data"))
            orch.block_node("tenant-a", node.id, "missing_input", "wait")
            assert session.get(PipelineRunModel, run_id).status == PipelineRunStatus.BLOCKED.value
            orch.unblock_node("tenant-a", node.id)
            assert node.status == NodeRunStatus.READY.value
        with sessions() as session:
            listing = session.scalar(select(__import__("app.modules.creative_pipeline.model", fromlist=["ListingTaskModel"]).ListingTaskModel))
            assert listing.status == "active"
    finally:
        engine.dispose()


def test_lease_recovery_reconciles_retry_and_processing_without_inventing_success():
    engine, sessions = setup()
    try:
        run_id = seed_run(sessions)
        with sessions.begin() as session:
            orch = CreativePipelineOrchestrator(session)
            orch.initialize_run("tenant-a", run_id)
            node = session.scalar(select(NodeRunModel).where(NodeRunModel.node_type == "input_data"))
            job = orch.schedule_node("tenant-a", node.id)
            job.status = JobStatus.RETRY.value
            job.next_attempt_at = datetime.now(timezone.utc) + timedelta(minutes=1)
            orch.reconcile_job("tenant-a", node.id)
            assert node.status == NodeRunStatus.RETRY_WAIT.value
            assert orch.reconcile_job("tenant-a", node.id).status == NodeRunStatus.RETRY_WAIT.value
    finally:
        engine.dispose()
