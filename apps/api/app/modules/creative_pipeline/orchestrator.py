from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.processing.types import JobStatus
from app.modules.creative_pipeline.constants import NodeRunStatus, NodeType, PipelineRunStatus
from app.modules.creative_pipeline.model import NodeRunModel, PipelineRunModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import JobOwnershipError, ProcessingRepository


PIPELINE_NODE_ORDER = (
    NodeType.INPUT_DATA,
    NodeType.IDEA_STORY,
    NodeType.PROMPT,
    NodeType.VIDEO_GENERATION,
    NodeType.VIDEO_OUTPUT,
    NodeType.WATERMARK_SMART_ENHANCE,
)
NODE_DEPENDENCIES = {
    node: PIPELINE_NODE_ORDER[index - 1] if index else None
    for index, node in enumerate(PIPELINE_NODE_ORDER)
}
CREATIVE_PIPELINE_JOB_TYPE = "creative_pipeline_node"
CREATIVE_PIPELINE_ENTITY_TYPE = "creative_pipeline_node_run"


class CreativePipelineStateError(RuntimeError):
    pass


class CreativePipelineOwnershipError(RuntimeError):
    pass


class CreativePipelineDependencyError(RuntimeError):
    pass


_ALLOWED_TRANSITIONS = {
    NodeRunStatus.PENDING.value: frozenset({NodeRunStatus.READY.value, NodeRunStatus.BLOCKED.value, NodeRunStatus.CANCELLED.value}),
    NodeRunStatus.READY.value: frozenset({NodeRunStatus.RUNNING.value, NodeRunStatus.BLOCKED.value, NodeRunStatus.CANCELLED.value}),
    NodeRunStatus.RUNNING.value: frozenset({NodeRunStatus.COMPLETED.value, NodeRunStatus.RETRY_WAIT.value, NodeRunStatus.FAILED.value, NodeRunStatus.BLOCKED.value, NodeRunStatus.CANCELLED.value}),
    NodeRunStatus.RETRY_WAIT.value: frozenset({NodeRunStatus.READY.value, NodeRunStatus.BLOCKED.value, NodeRunStatus.CANCELLED.value}),
    NodeRunStatus.BLOCKED.value: frozenset({NodeRunStatus.READY.value, NodeRunStatus.CANCELLED.value}),
    NodeRunStatus.COMPLETED.value: frozenset(),
    NodeRunStatus.FAILED.value: frozenset(),
    NodeRunStatus.CANCELLED.value: frozenset(),
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def transition(node: NodeRunModel, target: str) -> None:
    if target == node.status:
        return
    if target not in _ALLOWED_TRANSITIONS.get(node.status, frozenset()):
        raise CreativePipelineStateError(f"illegal node transition: {node.status} -> {target}")
    node.status = target


def derive_pipeline_status(run: PipelineRunModel, nodes: list[NodeRunModel]) -> str:
    if run.status == PipelineRunStatus.CANCELLED.value:
        return run.status
    statuses = {node.status for node in nodes}
    if NodeRunStatus.FAILED.value in statuses:
        return PipelineRunStatus.FAILED.value
    if NodeRunStatus.BLOCKED.value in statuses:
        return PipelineRunStatus.BLOCKED.value
    if nodes and all(status == NodeRunStatus.COMPLETED.value for status in statuses):
        return PipelineRunStatus.COMPLETED.value
    if NodeRunStatus.RUNNING.value in statuses:
        return PipelineRunStatus.RUNNING.value
    if NodeRunStatus.RETRY_WAIT.value in statuses:
        return PipelineRunStatus.RETRYING.value
    return PipelineRunStatus.QUEUED.value


class CreativePipelineOrchestrator:
    def __init__(self, session: Session, *, processing: ProcessingRepository | None = None):
        self.session = session
        self.processing = processing or ProcessingRepository(session)

    def _run(self, tenant_id: str, pipeline_run_id: str) -> PipelineRunModel:
        run = self.session.scalar(select(PipelineRunModel).where(
            PipelineRunModel.tenant_id == tenant_id,
            PipelineRunModel.id == pipeline_run_id,
        ))
        if run is None:
            raise CreativePipelineOwnershipError("pipeline run is unavailable")
        return run

    def _node(self, tenant_id: str, node_run_id: str) -> NodeRunModel:
        node = self.session.scalar(select(NodeRunModel).where(
            NodeRunModel.tenant_id == tenant_id,
            NodeRunModel.id == node_run_id,
        ))
        if node is None:
            raise CreativePipelineOwnershipError("node run is unavailable")
        return node

    def _nodes(self, run: PipelineRunModel) -> list[NodeRunModel]:
        return list(self.session.scalars(select(NodeRunModel).where(
            NodeRunModel.tenant_id == run.tenant_id,
            NodeRunModel.pipeline_run_id == run.id,
        ).order_by(NodeRunModel.created_at)))

    def initialize_run(self, tenant_id: str, pipeline_run_id: str) -> list[NodeRunModel]:
        run = self._run(tenant_id, pipeline_run_id)
        existing = {node.node_type: node for node in self._nodes(run)}
        if run.status in {PipelineRunStatus.FAILED.value, PipelineRunStatus.COMPLETED.value, PipelineRunStatus.CANCELLED.value} and not existing:
            return []
        for node_type in PIPELINE_NODE_ORDER:
            if node_type.value in existing:
                continue
            node = NodeRunModel(
                tenant_id=tenant_id,
                pipeline_run_id=run.id,
                node_type=node_type.value,
                status=NodeRunStatus.READY.value if node_type is NodeType.INPUT_DATA else NodeRunStatus.PENDING.value,
            )
            self.session.add(node)
            self.session.flush()
            existing[node_type.value] = node
        self._derive(run)
        return [existing[node_type.value] for node_type in PIPELINE_NODE_ORDER]

    def _dependency(self, node: NodeRunModel) -> NodeRunModel | None:
        index = next((i for i, item in enumerate(PIPELINE_NODE_ORDER) if item.value == node.node_type), None)
        if index is None or index == 0:
            return None
        return self.session.scalar(select(NodeRunModel).where(
            NodeRunModel.tenant_id == node.tenant_id,
            NodeRunModel.pipeline_run_id == node.pipeline_run_id,
            NodeRunModel.node_type == PIPELINE_NODE_ORDER[index - 1].value,
        ))

    def _assert_dependency_complete(self, node: NodeRunModel) -> None:
        dependency = self._dependency(node)
        if dependency is not None and dependency.status != NodeRunStatus.COMPLETED.value:
            raise CreativePipelineDependencyError("node dependency is not complete")

    def _derive(self, run: PipelineRunModel) -> str:
        status = derive_pipeline_status(run, self._nodes(run))
        if run.status != PipelineRunStatus.CANCELLED.value:
            run.status = status
        return status

    def schedule_node(self, tenant_id: str, node_run_id: str):
        node = self._node(tenant_id, node_run_id)
        run = self._run(tenant_id, node.pipeline_run_id)
        if run.status in {PipelineRunStatus.CANCELLED.value, PipelineRunStatus.FAILED.value, PipelineRunStatus.COMPLETED.value}:
            raise CreativePipelineStateError("pipeline run is terminal")
        if node.status != NodeRunStatus.READY.value:
            raise CreativePipelineStateError("only ready nodes can be scheduled")
        self._assert_dependency_complete(node)
        return self.processing.create_job(
            tenant_id=tenant_id,
            job_type=CREATIVE_PIPELINE_JOB_TYPE,
            entity_type=CREATIVE_PIPELINE_ENTITY_TYPE,
            entity_id=node.id,
            idempotency_key=f"creative_pipeline:node:{node.id}",
            payload={"pipeline_run_id": run.id, "node_run_id": node.id, "node_type": node.node_type},
            max_attempts=node.max_attempts,
        )

    def schedule_ready_nodes(self, tenant_id: str, pipeline_run_id: str):
        run = self._run(tenant_id, pipeline_run_id)
        return [self.schedule_node(tenant_id, node.id) for node in self._nodes(run) if node.status == NodeRunStatus.READY.value and (self._dependency(node) is None or self._dependency(node).status == NodeRunStatus.COMPLETED.value)]

    def begin_node_execution(self, tenant_id: str, node_run_id: str, processing_job_id: str, worker_id: str) -> NodeRunModel:
        node = self._node(tenant_id, node_run_id)
        run = self._run(tenant_id, node.pipeline_run_id)
        job = self.session.scalar(select(ProcessingJobModel).execution_options(populate_existing=True).where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.id == processing_job_id,
        ))
        if job is None or job.entity_type != CREATIVE_PIPELINE_ENTITY_TYPE or job.entity_id != node.id or job.job_type != CREATIVE_PIPELINE_JOB_TYPE:
            raise CreativePipelineOwnershipError("processing job does not own node")
        if job.status != JobStatus.PROCESSING.value or job.claimed_by != worker_id:
            raise CreativePipelineOwnershipError("worker does not own processing job")
        if run.status == PipelineRunStatus.CANCELLED.value:
            raise CreativePipelineStateError("cancelled run cannot execute")
        if node.status not in {NodeRunStatus.READY.value, NodeRunStatus.RETRY_WAIT.value}:
            if node.status == NodeRunStatus.RUNNING.value:
                return node
            raise CreativePipelineStateError("node is not executable")
        self._assert_dependency_complete(node)
        transition(node, NodeRunStatus.RUNNING.value)
        node.attempt_count = job.attempt_count
        node.max_attempts = job.max_attempts
        node.next_retry_at = None
        node.started_at = node.started_at or utcnow()
        self._derive(run)
        return node

    def complete_node(self, tenant_id: str, node_run_id: str, processing_job_id: str, worker_id: str, output_version: str | None = None) -> NodeRunModel:
        node = self._node(tenant_id, node_run_id)
        run = self._run(tenant_id, node.pipeline_run_id)
        if node.status == NodeRunStatus.COMPLETED.value:
            return node
        if run.status == PipelineRunStatus.CANCELLED.value:
            raise CreativePipelineStateError("cancelled run cannot complete")
        job = self.session.scalar(select(ProcessingJobModel).execution_options(populate_existing=True).where(ProcessingJobModel.tenant_id == tenant_id, ProcessingJobModel.id == processing_job_id))
        if job is None or job.entity_id != node.id or job.entity_type != CREATIVE_PIPELINE_ENTITY_TYPE:
            raise CreativePipelineOwnershipError("processing job does not own node")
        if job.status != JobStatus.COMPLETED.value:
            self.processing.complete_job(job_id=job.id, worker_id=worker_id)
        transition(node, NodeRunStatus.COMPLETED.value)
        node.completed_at = node.completed_at or utcnow()
        node.next_retry_at = None
        if output_version is not None:
            node.output_version = output_version
        self._unlock_next(run, node)
        self._derive(run)
        return node

    def _unlock_next(self, run: PipelineRunModel, node: NodeRunModel) -> NodeRunModel | None:
        index = next((i for i, item in enumerate(PIPELINE_NODE_ORDER) if item.value == node.node_type), None)
        if index is None or index + 1 >= len(PIPELINE_NODE_ORDER):
            return None
        nxt = self.session.scalar(select(NodeRunModel).where(NodeRunModel.tenant_id == run.tenant_id, NodeRunModel.pipeline_run_id == run.id, NodeRunModel.node_type == PIPELINE_NODE_ORDER[index + 1].value))
        if nxt and nxt.status == NodeRunStatus.PENDING.value:
            transition(nxt, NodeRunStatus.READY.value)
        if nxt and nxt.status == NodeRunStatus.READY.value:
            self.schedule_node(run.tenant_id, nxt.id)
        return nxt

    def fail_node_retryable(self, tenant_id: str, node_run_id: str, processing_job_id: str, worker_id: str, error_code: str, error_message: str) -> NodeRunModel:
        node = self._node(tenant_id, node_run_id)
        run = self._run(tenant_id, node.pipeline_run_id)
        job = self.processing.fail_job(job_id=processing_job_id, worker_id=worker_id, error_code=error_code, error_message=error_message)
        node.attempt_count = job.attempt_count
        node.last_error_code = job.last_error_code
        node.last_error_message = job.last_error_message
        if job.status == JobStatus.RETRY.value:
            transition(node, NodeRunStatus.RETRY_WAIT.value)
            node.next_retry_at = job.next_attempt_at
        else:
            transition(node, NodeRunStatus.FAILED.value)
            node.completed_at = node.completed_at or utcnow()
        self._derive(run)
        return node

    def fail_node_non_retryable(self, tenant_id: str, node_run_id: str, processing_job_id: str, worker_id: str, error_code: str, error_message: str) -> NodeRunModel:
        node = self._node(tenant_id, node_run_id)
        run = self._run(tenant_id, node.pipeline_run_id)
        job = self.processing.fail_job_non_retryable(job_id=processing_job_id, worker_id=worker_id, error_code=error_code, error_message=error_message)
        node.attempt_count = job.attempt_count
        node.last_error_code = job.last_error_code
        node.last_error_message = job.last_error_message
        transition(node, NodeRunStatus.FAILED.value)
        node.completed_at = node.completed_at or utcnow()
        self._derive(run)
        return node

    def retry_now(self, tenant_id: str, node_run_id: str) -> ProcessingJobModel:
        node = self._node(tenant_id, node_run_id)
        if node.status != NodeRunStatus.RETRY_WAIT.value:
            raise CreativePipelineStateError("retry now requires retry_wait")
        job = self.session.scalar(select(ProcessingJobModel).execution_options(populate_existing=True).where(ProcessingJobModel.tenant_id == tenant_id, ProcessingJobModel.entity_id == node.id, ProcessingJobModel.entity_type == CREATIVE_PIPELINE_ENTITY_TYPE))
        if job is None or job.status != JobStatus.RETRY.value:
            raise CreativePipelineStateError("retry job is unavailable")
        job.next_attempt_at = utcnow()
        transition(node, NodeRunStatus.READY.value)
        node.next_retry_at = None
        run = self._run(tenant_id, node.pipeline_run_id)
        self._derive(run)
        return job

    def block_node(self, tenant_id: str, node_run_id: str, error_code: str, error_message: str) -> NodeRunModel:
        node = self._node(tenant_id, node_run_id)
        run = self._run(tenant_id, node.pipeline_run_id)
        transition(node, NodeRunStatus.BLOCKED.value)
        node.last_error_code = error_code[:100]
        node.last_error_message = error_message[:1000]
        self._derive(run)
        return node

    def unblock_node(self, tenant_id: str, node_run_id: str) -> NodeRunModel:
        node = self._node(tenant_id, node_run_id)
        run = self._run(tenant_id, node.pipeline_run_id)
        if node.status != NodeRunStatus.BLOCKED.value:
            raise CreativePipelineStateError("unblock requires blocked node")
        self._assert_dependency_complete(node)
        transition(node, NodeRunStatus.READY.value)
        self._derive(run)
        self.schedule_node(tenant_id, node.id)
        return node

    def cancel_run(self, tenant_id: str, pipeline_run_id: str, requested_by: str | None = None, reason: str | None = None) -> PipelineRunModel:
        run = self._run(tenant_id, pipeline_run_id)
        if run.status == PipelineRunStatus.CANCELLED.value:
            return run
        now = utcnow()
        nodes = self._nodes(run)
        for node in nodes:
            if node.status in {NodeRunStatus.PENDING.value, NodeRunStatus.READY.value, NodeRunStatus.RETRY_WAIT.value, NodeRunStatus.RUNNING.value, NodeRunStatus.BLOCKED.value}:
                job = self.session.scalar(select(ProcessingJobModel).execution_options(populate_existing=True).where(ProcessingJobModel.tenant_id == tenant_id, ProcessingJobModel.entity_id == node.id, ProcessingJobModel.entity_type == CREATIVE_PIPELINE_ENTITY_TYPE))
                if job is not None:
                    if job.status in {JobStatus.PENDING.value, JobStatus.RETRY.value}:
                        self.processing.cancel_unstarted_job(tenant_id=tenant_id, job_id=job.id, actor_id=requested_by or "system", reason=reason or "pipeline cancelled", now=now)
                    elif job.status == JobStatus.PROCESSING.value:
                        job.cancellation_requested = True
                        job.cancel_requested_at = now
                        job.cancel_requested_by = requested_by
                        job.cancellation_reason = reason
                transition(node, NodeRunStatus.CANCELLED.value)
        run.status = PipelineRunStatus.CANCELLED.value
        run.completed_at = run.completed_at or now
        return run

    def reconcile_job(self, tenant_id: str, node_run_id: str) -> NodeRunModel:
        node = self._node(tenant_id, node_run_id)
        run = self._run(tenant_id, node.pipeline_run_id)
        job = self.session.scalar(select(ProcessingJobModel).execution_options(populate_existing=True).where(ProcessingJobModel.tenant_id == tenant_id, ProcessingJobModel.entity_id == node.id, ProcessingJobModel.entity_type == CREATIVE_PIPELINE_ENTITY_TYPE))
        if job is None or run.status == PipelineRunStatus.CANCELLED.value:
            return node
        if job.status == JobStatus.PROCESSING.value and node.status in {NodeRunStatus.READY.value, NodeRunStatus.RETRY_WAIT.value}:
            transition(node, NodeRunStatus.RUNNING.value)
            node.attempt_count = job.attempt_count
        elif job.status == JobStatus.RETRY.value and node.status in {NodeRunStatus.READY.value, NodeRunStatus.RUNNING.value}:
            # Recovery may observe a queue retry before the worker callback
            # persisted RUNNING; this is the durable queue-to-node bridge.
            node.status = NodeRunStatus.RETRY_WAIT.value
            node.attempt_count = job.attempt_count
            node.next_retry_at = job.next_attempt_at
        elif job.status == JobStatus.FAILED.value and node.status not in {NodeRunStatus.COMPLETED.value, NodeRunStatus.CANCELLED.value}:
            transition(node, NodeRunStatus.FAILED.value)
            node.attempt_count = job.attempt_count
            node.completed_at = node.completed_at or utcnow()
        self._derive(run)
        return node
