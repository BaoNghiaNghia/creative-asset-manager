from __future__ import annotations
import re
from collections import Counter
from datetime import datetime
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from app.modules.authorization.folder_scope import ViewerFolderScopeService
from app.modules.authorization.principal import CurrentPrincipal, is_pure_viewer
from app.modules.creative_pipeline.constants import NodeType
from app.modules.creative_pipeline.discovery import CreativePipelineDiscoveryResult, CreativePipelineDiscoveryScanner
from app.modules.creative_pipeline.model import (
    ArtifactModel, GenerationRunModel, ListingTaskModel, NodeRunModel,
    PipelineRunModel, SourceGroupModel,
)
from app.modules.creative_pipeline.platforms import platform_profile
from app.modules.creative_pipeline.repository import CreativePipelineRepository
from app.modules.creative_pipeline.constants import PipelineRunStatus, PipelineTriggerType
from app.modules.creative_pipeline.orchestrator import CreativePipelineOrchestrator, CreativePipelineStateError

NODE_TYPES = tuple(node.value for node in (
    NodeType.INPUT_DATA, NodeType.IDEA_STORY, NodeType.PROMPT,
    NodeType.VIDEO_GENERATION, NodeType.VIDEO_OUTPUT, NodeType.WATERMARK_SMART_ENHANCE,
))
SAFE_METADATA = frozenset({
    "knowledge_snapshot_id", "idea_story_schema_version", "prompt_template_version",
    "target_provider", "target_model", "generation_number", "raw_artifact_id",
    "raw_content_hash", "enhancement_policy_version", "enhancement_engine",
    "enhancement_engine_version",
})
NODE_TYPE_INDEX = {node_type: index for index, node_type in enumerate(NODE_TYPES)}
STATUS_KEYS = ("queued", "running", "retrying", "blocked", "failed", "completed", "cancelled")

def _safe_text(value, limit=1000):
    if value is None: return None
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value))[:limit]

def _metadata(value):
    if not isinstance(value, dict): return {}
    return {key: value[key] for key in SAFE_METADATA if key in value}

class CreativePipelineApiService:
    def __init__(self, session):
        self.session = session

    def _group(self, tenant, group_id):
        return self.session.scalar(select(SourceGroupModel).where(SourceGroupModel.tenant_id == tenant, SourceGroupModel.id == group_id))

    def _listing(self, tenant, listing_id):
        return self.session.scalar(select(ListingTaskModel).where(ListingTaskModel.tenant_id == tenant, ListingTaskModel.id == listing_id))

    def _run(self, tenant, run_id):
        return self.session.scalar(select(PipelineRunModel).where(PipelineRunModel.tenant_id == tenant, PipelineRunModel.id == run_id))

    def _scope_allows(self, principal, group, listing=None):
        if group is None: return False
        access = ViewerFolderScopeService(self.session).access(
            tenant_id=principal.active_tenant_id, membership_id=principal.membership_id,
            roles=principal.effective_roles, external_source_id=group.external_source_id,
        )
        if not access.restricted: return True
        target = listing.external_folder_id if listing is not None else group.external_folder_id
        if not target: return False
        return ViewerFolderScopeService(self.session).allows_external_asset(
            tenant_id=principal.active_tenant_id, access=access, external_asset_id=target,
        )

    def require_group(self, principal, group_id):
        group = self._group(principal.active_tenant_id, group_id)
        return group if self._scope_allows(principal, group) else None

    def require_listing(self, principal, listing_id):
        listing = self._listing(principal.active_tenant_id, listing_id)
        group = self._group(principal.active_tenant_id, listing.source_group_id) if listing else None
        return (listing, group) if listing and self._scope_allows(principal, group, listing) else (None, None)

    def require_run(self, principal, run_id):
        run = self._run(principal.active_tenant_id, run_id)
        listing = self._listing(principal.active_tenant_id, run.listing_task_id) if run else None
        group = self._group(principal.active_tenant_id, listing.source_group_id) if listing else None
        return (run, listing, group) if run and listing and self._scope_allows(principal, group, listing) else (None, None, None)

    def _current_run(self, tenant, listing_id):
        return self.session.scalar(select(PipelineRunModel).where(
            PipelineRunModel.tenant_id == tenant, PipelineRunModel.listing_task_id == listing_id
        ).order_by(PipelineRunModel.run_number.desc()))

    def node_summary(self, run):
        rows = {row.node_type: row for row in self.session.scalars(select(NodeRunModel).where(
            NodeRunModel.tenant_id == run.tenant_id, NodeRunModel.pipeline_run_id == run.id))}
        result = []
        for node_type in NODE_TYPES:
            row = rows.get(node_type)
            inherited = bool(run.branch_start_node and NODE_TYPE_INDEX[node_type] < NODE_TYPE_INDEX[run.branch_start_node])
            result.append({"id": row.id if row else None, "node_type": node_type, "inherited": inherited,
                "status": row.status if row else "not_initialized", "attempt_count": row.attempt_count if row else 0,
                "max_attempts": row.max_attempts if row else None, "next_retry_at": row.next_retry_at if row else None,
                "output_version": row.output_version if row else None, "last_error_code": _safe_text(row.last_error_code,100) if row else None,
                "last_error_message": _safe_text(row.last_error_message) if row else None})
        return result

    def run_summary(self, run, listing=None, include_children=True):
        nodes = self.node_summary(run)
        generations = self.generation_summaries(run) if include_children else []
        artifacts = self.artifact_summaries(run) if include_children else []
        return {"id": run.id, "run_number": run.run_number, "parent_run_id": run.parent_run_id,
            "branch_start_node": run.branch_start_node, "trigger_type": run.trigger_type,
            "triggered_by": _safe_text(run.triggered_by,255), "status": run.status,
            "knowledge_snapshot_id": run.knowledge_snapshot_id, "started_at": run.started_at,
            "completed_at": run.completed_at, "created_at": run.created_at, "updated_at": run.updated_at,
            "listing_id": run.listing_task_id, "nodes": nodes, "generations": generations, "artifacts": artifacts}

    def generation_summaries(self, run):
        rows = self.session.scalars(select(GenerationRunModel).where(
            GenerationRunModel.tenant_id == run.tenant_id, GenerationRunModel.pipeline_run_id == run.id
        ).order_by(GenerationRunModel.aspect_ratio, GenerationRunModel.provider, GenerationRunModel.generation_number, GenerationRunModel.id))
        return [{"id": row.id, "provider": row.provider, "model": row.model, "aspect_ratio": row.aspect_ratio,
            "generation_number": row.generation_number, "status": row.status, "attempt_count": row.attempt_count,
            "max_attempts": row.max_attempts, "submitted_at": row.submitted_at, "started_at": row.started_at,
            "completed_at": row.completed_at, "last_error_code": _safe_text(row.last_error_code,100),
            "last_error_message": _safe_text(row.last_error_message)} for row in rows]

    def artifact_summaries(self, run):
        rows = self.session.scalars(select(ArtifactModel).where(
            ArtifactModel.tenant_id == run.tenant_id, ArtifactModel.pipeline_run_id == run.id
        ).order_by(ArtifactModel.artifact_type, ArtifactModel.aspect_ratio, ArtifactModel.version, ArtifactModel.variant_key, ArtifactModel.id))
        return [self.artifact_summary(row) for row in rows]

    @staticmethod
    def artifact_summary(row):
        return {"id": row.id, "artifact_type": row.artifact_type, "status": row.status, "version": row.version,
            "variant_key": row.variant_key, "aspect_ratio": row.aspect_ratio, "relative_path": row.relative_path,
            "content_hash": row.content_hash, "mime_type": row.mime_type, "size_bytes": row.size_bytes,
            "model_provider": row.model_provider, "model_name": row.model_name, "generation_run_id": row.generation_run_id,
            "node_run_id": row.node_run_id, "created_at": row.created_at, "available_at": row.available_at,
            "metadata": _metadata(row.metadata_json)}

    def group_summary(self, group):
        listings = list(self.session.scalars(select(ListingTaskModel).where(ListingTaskModel.tenant_id == group.tenant_id, ListingTaskModel.source_group_id == group.id)))
        runs = self.session.scalars(select(PipelineRunModel).where(PipelineRunModel.tenant_id == group.tenant_id, PipelineRunModel.listing_task_id.in_([x.id for x in listings])).order_by(PipelineRunModel.listing_task_id, PipelineRunModel.run_number.desc())).all() if listings else []
        latest = {}
        for row in runs: latest.setdefault(row.listing_task_id, row)
        counts = Counter(row.status for row in latest.values())
        return {"id": group.id, "platform": group.platform, "name": group.name, "active": group.active,
            "scan_enabled": group.scan_enabled, "last_scan_at": group.last_scan_at,
            "last_successful_scan_at": group.last_successful_scan_at, "listing_count": len(listings),
            "active_listing_count": sum(x.status == "active" for x in listings),
            "missing_source_count": sum(x.status == "missing_source" for x in listings),
            "current_runs": {key: counts.get(key,0) for key in STATUS_KEYS}}

    def listing_summary(self, listing, group, include_detail=False):
        run = self._current_run(listing.tenant_id, listing.id)
        payload = {"id": listing.id, "listing_key": listing.listing_key, "folder_name": listing.folder_name,
            "folder_path": listing.folder_path, "platform": listing.platform, "source_group_id": group.id,
            "source_group": {"id": group.id, "name": group.name, "platform": group.platform},
            "source_status": listing.status, "pipeline_status": run.status if run else None,
            "required_aspect_ratios": list(platform_profile(listing.platform).required_aspect_ratios),
            "current_run": self.run_summary(run, listing) if run else None}
        if include_detail:
            payload["capabilities"] = self.listing_capabilities(listing)
            payload["artifact_count"] = self.session.scalar(select(func.count()).select_from(ArtifactModel).where(ArtifactModel.tenant_id == listing.tenant_id, ArtifactModel.pipeline_run_id == run.id)) if run else 0
            payload["generation_count"] = self.session.scalar(select(func.count()).select_from(GenerationRunModel).where(GenerationRunModel.tenant_id == listing.tenant_id, GenerationRunModel.pipeline_run_id == run.id)) if run else 0
        return payload

    @staticmethod
    def capabilities():
        return {"scan_group": True, "retry_wait_node": True, "cancel_run": True,
            "start_uninitialized_run": False, "start_new_run": False,
            "terminal_failed_retry": False, "regenerate_idea": False,
            "regenerate_prompt": False, "generate_another_video": False,
            "retry_failed_enhance": False}

    def listing_capabilities(self, listing):
        current = self._current_run(listing.tenant_id, listing.id)
        caps = dict(self.capabilities())
        if current is None:
            caps["start_uninitialized_run"] = True
            return caps
        caps["start_uninitialized_run"] = current.run_number == 1 and current.status == "queued" and not self.session.scalar(select(NodeRunModel.id).where(NodeRunModel.tenant_id==listing.tenant_id, NodeRunModel.pipeline_run_id==current.id))
        terminal = current.status in {"completed", "failed", "cancelled"}
        caps["start_new_run"] = terminal and listing.status == "active"
        if terminal:
            from app.modules.creative_pipeline.lineage import CreativePipelineLineageResolver
            resolver=CreativePipelineLineageResolver(self.session)
            caps["regenerate_idea"] = resolver.effective_artifact(current, "input_snapshot") is not None and resolver.effective_knowledge_snapshot(current)[0] is not None
            caps["regenerate_prompt"] = caps["regenerate_idea"] and resolver.effective_artifact(current, "idea_story") is not None
            caps["generate_another_video"] = caps["regenerate_prompt"] and all(resolver.effective_prompt_artifacts(current))
        return caps

    def create_branch_run(self, principal, listing, action, idempotency_key):
        key = str(idempotency_key).strip()
        if not key or len(key) > 255:
            raise ValueError("invalid_idempotency_key")
        # PostgreSQL serializes branch allocation per listing. Repeat the
        # idempotency lookup after acquiring the lock to close request races.
        locked_listing = self.session.scalar(select(ListingTaskModel).where(
            ListingTaskModel.tenant_id == principal.active_tenant_id,
            ListingTaskModel.id == listing.id,
        ).with_for_update())
        if locked_listing is None:
            raise ValueError("listing_source_unavailable")
        listing = locked_listing
        existing = self.session.scalar(select(PipelineRunModel).where(
            PipelineRunModel.tenant_id == principal.active_tenant_id,
            PipelineRunModel.listing_task_id == listing.id,
            PipelineRunModel.operator_idempotency_key == key,
        ))
        branch = {"run": "input_data", "regenerate-idea": "idea_story", "regenerate-prompt": "prompt", "generate-another-video": "video_generation"}.get(action)
        if branch is None: raise ValueError("unsupported_branch_action")
        if existing is not None:
            if existing.branch_start_node != branch:
                raise ValueError("creative_pipeline_idempotency_conflict")
            return existing
        current = self._current_run(principal.active_tenant_id, listing.id)
        if action != "run" and current is None: raise ValueError("parent_run_required")
        if current is not None and current.status not in {"completed", "failed", "cancelled"}:
            raise CreativePipelineStateError("creative_pipeline_active_run_exists")
        if listing.status != "active":
            raise ValueError("listing_source_unavailable")
        from app.modules.creative_pipeline.lineage import CreativePipelineLineageResolver
        resolver = CreativePipelineLineageResolver(self.session)
        if action == "regenerate-idea" and current is not None:
            resolver=CreativePipelineLineageResolver(self.session)
            if resolver.effective_artifact(current, "input_snapshot") is None or resolver.effective_knowledge_snapshot(current)[0] is None: raise ValueError("effective_input_unavailable")
        if action == "regenerate-prompt" and current is not None:
            resolver=CreativePipelineLineageResolver(self.session)
            if resolver.effective_artifact(current, "idea_story") is None: raise ValueError("effective_idea_unavailable")
        if action == "generate-another-video" and current is not None:
            resolver=CreativePipelineLineageResolver(self.session)
            if not all(resolver.effective_prompt_artifacts(current)): raise ValueError("effective_prompt_unavailable")
        run_number = (self.session.scalar(select(func.max(PipelineRunModel.run_number)).where(PipelineRunModel.tenant_id==principal.active_tenant_id, PipelineRunModel.listing_task_id==listing.id)) or 0) + 1
        try:
            with self.session.begin_nested():
                run = PipelineRunModel(tenant_id=principal.active_tenant_id, listing_task_id=listing.id, run_number=run_number,
                    status="queued", trigger_type=PipelineTriggerType.REGENERATE.value if action != "run" else PipelineTriggerType.MANUAL.value,
                    triggered_by=principal.actor_id, parent_run_id=current.id if current else None, branch_start_node=branch, operator_idempotency_key=key, knowledge_snapshot_id=(resolver.effective_knowledge_snapshot(current)[0] if action != "run" else None))
                self.session.add(run)
                self.session.flush()
        except IntegrityError:
            existing = self.session.scalar(select(PipelineRunModel).where(
                PipelineRunModel.tenant_id == principal.active_tenant_id,
                PipelineRunModel.listing_task_id == listing.id,
                PipelineRunModel.operator_idempotency_key == key,
            ))
            if existing is not None:
                if existing.branch_start_node != branch:
                    raise ValueError("creative_pipeline_idempotency_conflict")
                return existing
            raise
        orch=CreativePipelineOrchestrator(self.session)
        orch.initialize_branch(principal.active_tenant_id, run.id, branch, current.id if current else None)
        orch.schedule_ready_nodes(principal.active_tenant_id, run.id)
        return run

    async def scan_group(self, principal, group):
        from app.modules.assets.source_credentials import source_credential_contract
        from app.modules.explorer.tenant_source import TenantSourceResolver
        from app.modules.creative_pipeline.provider import ExplorerFolderListingGateway
        access = await TenantSourceResolver(self.session).resolve(tenant_id=principal.active_tenant_id, external_source_id=group.external_source_id)
        contract = source_credential_contract(access.source_type)
        gateway = ExplorerFolderListingGateway(contract.adapter_key, access.access_token)
        scanner = CreativePipelineDiscoveryScanner(self.session, page_size=100)
        async with gateway:
            entries = await gateway.list_all_child_folders(group.external_folder_id, page_size=100)
            result = CreativePipelineDiscoveryResult()
            result.groups_seen = 1
            await scanner._reconcile_listings(group=group, tenant_id=principal.active_tenant_id, entries=entries, result=result)
            group.last_scan_at = datetime.now().astimezone()
            group.last_successful_scan_at = group.last_scan_at
            self.session.commit()
            return result
