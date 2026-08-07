from __future__ import annotations
from datetime import datetime, timezone
import logging
import httpx
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.ai_governance.repository import AiGovernanceRepository
from app.modules.authorization.folder_scope import ViewerFolderScopeService
from app.modules.explorer.breadcrumb import resolve_breadcrumb
from app.providers.google.auth import get_access_token as get_google_token
from app.providers.google.drive import GoogleDriveClient
from app.modules.authorization.principal import (
    CurrentPrincipal, require_authenticated_principal, require_permission,
    require_principal_permission,
)
from app.modules.asset_details.schema import AcceptedAssetAction, AssetActionRequest, AssetDetailsResponse
from app.modules.asset_details.media_dimensions import MediaDimensionsResolver
from app.modules.explorer.schema import LocationBreadcrumbNode
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.assets.source_url import resolve_source_web_url
from app.modules.explorer.media_types import infer_media_type, is_previewable_media
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.pipeline.repository import AssetPipelineRepository
from app.modules.pipeline.state import FAILURE_STATES
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.storage.model import AssetStorageObjectModel

router = APIRouter(prefix="/api/v1", tags=["asset-details"])
logger = logging.getLogger(__name__)
MAX_JSON_NODES = 1500
MAX_JSON_DEPTH = 10
ASSETS_READ = require_permission("assets.read")



def iso(value):
    return value.isoformat() if value else None

def safe_url(value):
    if not value:
        return None
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host + (f":{parsed.port}" if parsed.port else "")
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def source_preview_url(
    source: SourceAssetModel,
    external_source: ExternalSourceModel,
    asset_mime_type: str | None,
) -> str | None:
    """Return a source-media proxy URL only for previewable, active files."""
    mime_type = infer_media_type(source.filename, source.mime_type or asset_mime_type)
    if source.deleted_at is not None or not is_previewable_media(source.filename, mime_type):
        return None
    provider = "sharepoint" if "sharepoint" in (external_source.source_type or "").lower() else "google-drive"
    return (
        f"/api/explorer/media/{quote(source.external_asset_id, safe='')}"
        f"?provider={provider}&external_source_id={quote(source.external_source_id, safe='')}"
    )

def bounded(value: Any, depth=0, budget=None):
    budget = budget or [MAX_JSON_NODES]
    if budget[0] <= 0 or depth >= MAX_JSON_DEPTH:
        return "[truncated]"
    budget[0] -= 1
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:300]:
            if any(secret in str(key).lower() for secret in ("token", "secret", "credential", "signed_url", "raw_response")):
                continue
            result[str(key)[:200]] = bounded(item, depth + 1, budget)
        return result
    if isinstance(value, list):
        return [bounded(item, depth + 1, budget) for item in value[:300]]
    return value[:4000] if isinstance(value, str) else value

def analysis_doc(item, include_cost):
    return {
        "id": item.id, "status": item.status, "processing_stage": item.processing_stage,
        "metadata_profile": item.metadata_profile, "metadata_profile_version": item.metadata_profile_version,
        "prompt_version": item.prompt_version, "pipeline_version": item.pipeline_version,
        "ai_provider": item.ai_provider, "ai_model": item.ai_model,
        "search_projection_version": item.search_projection_version,
        "metadata_json": bounded(item.metadata_json), "search_projection": bounded(item.search_projection),
        "validation_errors": bounded(item.validation_errors_json),
        "attempt_count": item.attempt_count, "forced": item.forced,
        "last_error_code": item.last_error_code, "last_error_message": item.last_error_message,
        "usage": bounded(item.usage_json or {}) if include_cost else None,
        "created_at": iso(item.created_at), "started_at": iso(item.started_at), "completed_at": iso(item.completed_at),
    }

def related_ids(session, tenant, asset_id):
    analyses = set(session.scalars(select(AssetAiAnalysisModel.id).where(AssetAiAnalysisModel.tenant_id == tenant, AssetAiAnalysisModel.asset_id == asset_id)))
    pipelines = set(session.scalars(select(AssetPipelineModel.id).where(AssetPipelineModel.tenant_id == tenant, AssetPipelineModel.asset_id == asset_id)))
    sources = set(session.scalars(select(AssetSourceLinkModel.source_asset_id).where(AssetSourceLinkModel.tenant_id == tenant, AssetSourceLinkModel.asset_id == asset_id)))
    return analyses, pipelines, sources

@router.get("/assets/{asset_id}", response_model=AssetDetailsResponse)
async def details(request: Request, asset_id: str, external_source_id: str | None = Query(None), analysis_offset: int = Query(0, ge=0), analysis_limit: int = Query(20, ge=1, le=100), job_offset: int = Query(0, ge=0), job_limit: int = Query(50, ge=1, le=200), principal: CurrentPrincipal = Depends(ASSETS_READ)):
    tenant = principal.active_tenant_id
    include_cost = principal.platform_admin or bool({"ai_operations.read", "ai_budget.read"}.intersection(principal.effective_permissions))
    can_administer = principal.platform_admin or bool({"ai_analysis.run", "ai_jobs.retry", "ai_jobs.cancel", "search.rebuild"}.intersection(principal.effective_permissions))
    with SessionLocal() as session:
        asset = session.scalar(select(AssetModel).where(AssetModel.id == asset_id, AssetModel.tenant_id == tenant))
        if asset is None:
            raise HTTPException(404, "Asset not found")
        source_rows = session.execute(select(SourceAssetModel, ExternalSourceModel).join(AssetSourceLinkModel, AssetSourceLinkModel.source_asset_id == SourceAssetModel.id).join(ExternalSourceModel, ExternalSourceModel.id == SourceAssetModel.external_source_id).where(AssetSourceLinkModel.tenant_id == tenant, AssetSourceLinkModel.asset_id == asset_id, SourceAssetModel.tenant_id == tenant, ExternalSourceModel.tenant_id == tenant,
            *( [SourceAssetModel.external_source_id == external_source_id] if external_source_id else [] ),
        ).order_by(SourceAssetModel.created_at)).all()
        scope_service = ViewerFolderScopeService(session)
        visible_source_rows = []
        for source, external_source in source_rows:
            access = scope_service.access(
                tenant_id=tenant,
                membership_id=principal.membership_id,
                roles=principal.effective_roles,
                external_source_id=external_source.id,
            )
            if not access.restricted or scope_service.allows_external_asset(
                tenant_id=tenant,
                access=access,
                external_asset_id=source.external_asset_id,
            ):
                visible_source_rows.append((source, external_source))
        if source_rows and not visible_source_rows:
            raise HTTPException(
                status_code=403,
                detail={"code": "viewer_folder_scope_denied", "message": "Asset is outside the viewer folder scope."},
            )
        source_rows = visible_source_rows
        image_width: int | None = None
        image_height: int | None = None
        resolution_source = "unavailable"
        selected_image_source = next(
            ((source, external_source) for source, external_source in source_rows
             if (source.mime_type or asset.mime_type or "").lower().startswith("image/")),
            None,
        )
        if selected_image_source:
            image_source, image_external = selected_image_source
            dimensions = await MediaDimensionsResolver(session).resolve(
                source=image_source, external=image_external, token=await get_google_token(request) if image_external.source_type == "google_drive" else None
            )
            image_width, image_height, resolution_source = dimensions.width, dimensions.height, dimensions.source
        location_breadcrumb: list[dict[str, str]] = []
        location_status = "unavailable" if source_rows else "unavailable"
        location_unavailable = bool(source_rows)
        for source, external_source in source_rows:
            rows = list(session.scalars(select(SourceAssetModel).where(
                SourceAssetModel.tenant_id == tenant,
                SourceAssetModel.external_source_id == source.external_source_id,
                SourceAssetModel.deleted_at.is_(None),
            )))
            folders = {}
            for row in rows:
                metadata = row.source_metadata if isinstance(row.source_metadata, dict) else {}
                parents = metadata.get("parents") if isinstance(metadata.get("parents"), list) else [metadata.get("parent_id")]
                folders[str(row.external_asset_id)] = {
                    "name": row.filename or "Folder",
                    "parent_id": next((str(value) for value in parents if value), None),
                }
            metadata = external_source.source_metadata if isinstance(external_source.source_metadata, dict) else {}
            root_id = str(metadata.get("root_folder_id") or metadata.get("folder_id") or "root")
            folders.setdefault("root", {"name": str(external_source.display_name or "My Drive"), "parent_id": None})
            access = scope_service.access(tenant_id=tenant, membership_id=principal.membership_id, roles=principal.effective_roles, external_source_id=source.external_source_id)
            permitted = set(access.folder_ids) if access.restricted else None
            source_parents = source.source_metadata if isinstance(source.source_metadata, dict) else {}
            parent_id = next((str(value) for value in source_parents.get("parents", []) if value), None) if isinstance(source_parents.get("parents"), list) else str(source_parents.get("parent_id") or "")
            location_breadcrumb = resolve_breadcrumb(item_id=source.external_asset_id, parent_id=parent_id, folders=folders, source_root_id=root_id, permitted_root_ids=permitted)
            if location_breadcrumb:
                location_status = "resolved"
                location_unavailable = False
                break
            # Local metadata can lag a newly synchronized parent. Resolve with
            # the same tenant source credential, then persist each folder row.
            if external_source.source_type == "google_drive" and (not access.restricted or access.allows_external_asset(tenant_id=tenant, external_asset_id=source.external_asset_id)):
                try:
                    token = await get_google_token(request)
                    if token:
                        current = parent_id
                        provider_folders = {}
                        async with GoogleDriveClient(token) as client:
                            for depth in range(64):
                                if not current:
                                    break
                                if current in provider_folders:
                                    logger.warning("Folder breadcrumb cycle file_provider_id=%s missing_parent_id=%s external_source_id=%s depth=%s", source.external_asset_id, current, source.external_source_id, depth)
                                    break
                                node = await client.get_breadcrumb_metadata(current)
                                provider_folders[current] = {"name": node.name, "parent_id": node.parent_id}
                                if current == root_id or (root_id == "root" and node.parent_id in {None, "root"}):
                                    if root_id == "root":
                                        root_id = current
                                    break
                                current = node.parent_id
                        location_breadcrumb = resolve_breadcrumb(item_id=source.external_asset_id, parent_id=parent_id, folders={**folders, **provider_folders}, source_root_id=root_id, permitted_root_ids=permitted)
                        if location_breadcrumb:
                            from app.modules.assets.repository import AssetRegistryRepository
                            repository = AssetRegistryRepository(session)
                            for folder_id, folder in provider_folders.items():
                                repository.upsert_source_asset(
                                    tenant_id=tenant, external_source_id=source.external_source_id,
                                    external_asset_id=folder_id, filename=folder["name"],
                                    mime_type="application/vnd.google-apps.folder",
                                    source_metadata={"parents": [folder["parent_id"]] if folder["parent_id"] else [], "is_folder": True},
                                )
                            session.commit()
                            location_status = "resolved"
                            location_unavailable = False
                            break
                except httpx.HTTPStatusError as exc:
                    status_code = exc.response.status_code
                    reason = "provider_forbidden" if status_code in {401, 403} else "provider_not_found" if status_code == 404 else "provider_error"
                    logger.warning("Folder breadcrumb %s file_provider_id=%s missing_parent_id=%s external_source_id=%s depth=%s", reason, source.external_asset_id, parent_id, source.external_source_id, depth if "depth" in locals() else 0)
                except (httpx.HTTPError, ValueError) as exc:
                    logger.warning("Folder breadcrumb provider_error file_provider_id=%s missing_parent_id=%s external_source_id=%s error=%s", source.external_asset_id, parent_id, source.external_source_id, type(exc).__name__)
            location_unavailable = True
        sources = [{"source_asset_id": s.id, "external_source_id": s.external_source_id, "external_asset_id": s.external_asset_id, "source_type": e.source_type, "source_key": e.source_key, "display_name": e.display_name, "filename": s.filename, "mime_type": s.mime_type or asset.mime_type, "size_bytes": s.size_bytes, "provider_checksum": s.provider_checksum, "provider_version": s.provider_version, "preview_url": source_preview_url(s, e, asset.mime_type), "web_url": resolve_source_web_url(provider="sharepoint" if e.source_type == "sharepoint" else "google-drive", external_asset_id=s.external_asset_id, source_metadata=s.source_metadata), "deleted": s.deleted_at is not None, "created_at": iso(s.source_created_at), "modified_at": iso(s.source_modified_at)} for s, e in source_rows]
        storage = [{"id": row.id, "provider": row.storage_provider, "status": row.status, "remote_file_id": row.remote_file_id, "remote_folder_id": row.remote_folder_id, "web_url": safe_url(row.web_url), "verified": row.status == "stored" and bool(row.remote_file_id), "attempt_count": row.attempt_count, "last_error_code": row.last_error_code, "last_error_message": row.last_error_message, "stored_at": iso(row.stored_at)} for row in session.scalars(select(AssetStorageObjectModel).where(AssetStorageObjectModel.tenant_id == tenant, AssetStorageObjectModel.asset_id == asset_id).order_by(AssetStorageObjectModel.updated_at.desc()))]
        base_analysis = select(AssetAiAnalysisModel).where(AssetAiAnalysisModel.tenant_id == tenant, AssetAiAnalysisModel.asset_id == asset_id)
        analysis_total = int(session.scalar(select(func.count()).select_from(base_analysis.subquery())) or 0)
        analyses = list(session.scalars(base_analysis.order_by(AssetAiAnalysisModel.created_at.desc(), AssetAiAnalysisModel.id.desc()).offset(analysis_offset).limit(analysis_limit)))
        aids, pids, sids = related_ids(session, tenant, asset_id)
        entity_ids = {asset_id, *aids, *pids, *sids}
        base_jobs = select(ProcessingJobModel).where(ProcessingJobModel.tenant_id == tenant, ProcessingJobModel.entity_id.in_(entity_ids))
        job_total = int(session.scalar(select(func.count()).select_from(base_jobs.subquery())) or 0)
        jobs = [{"id": row.id, "job_type": row.job_type, "entity_type": row.entity_type, "entity_id": row.entity_id, "status": row.status, "attempt_count": row.attempt_count, "max_attempts": row.max_attempts, "provider_key": row.provider_key, "last_error_code": row.last_error_code, "last_error_message": row.last_error_message, "next_attempt_at": iso(row.next_attempt_at), "created_at": iso(row.created_at), "completed_at": iso(row.completed_at), "cancelable": row.status in {"pending", "retry"}} for row in session.scalars(base_jobs.order_by(ProcessingJobModel.created_at.desc()).offset(job_offset).limit(job_limit))]
        pipeline_rows = list(session.scalars(select(AssetPipelineModel).where(AssetPipelineModel.tenant_id == tenant, AssetPipelineModel.asset_id == asset_id).order_by(AssetPipelineModel.updated_at.desc())))
        pipelines = [{"id": row.id, "state": row.state, "origin_type": row.origin_type, "origin_id": row.origin_id, "analysis_id": row.analysis_id, "last_error_code": row.last_error_code, "last_error_message": row.last_error_message, "failure_retryable": row.failure_retryable, "created_at": iso(row.created_at), "updated_at": iso(row.updated_at), "completed_at": iso(row.completed_at)} for row in pipeline_rows]
        lifecycle = pipeline_rows[0].state if pipeline_rows else ("metadata_ready" if analyses and analyses[0].status == "completed" else "discovered")
        return {"asset": {"id": asset.id, "content_hash": asset.content_hash, "analysis_image_hash": asset.analysis_image_hash, "mime_type": asset.mime_type, "size_bytes": asset.size_bytes, "created_at": iso(asset.created_at), "updated_at": iso(asset.updated_at)}, "sources": sources, "storage": storage, "active_analysis": analysis_doc(analyses[0], include_cost) if analyses else None, "analysis_history": [analysis_doc(item, include_cost) for item in analyses], "analysis_total": analysis_total, "jobs": jobs, "job_total": job_total, "pipelines": pipelines, "lifecycle_status": lifecycle, "location_breadcrumb": [LocationBreadcrumbNode(id=str(node["id"]), name=str(node["name"])) for node in location_breadcrumb], "location_status": location_status, "location_unavailable": location_unavailable, "image_width": image_width, "image_height": image_height, "resolution_source": resolution_source, "resolution_status": "available" if image_width and image_height else "unavailable", "can_administer": can_administer, "limits": {"max_json_nodes": MAX_JSON_NODES, "max_json_depth": MAX_JSON_DEPTH}}

@router.post("/admin/assets/{asset_id}/actions", response_model=AcceptedAssetAction, status_code=202)
def action(asset_id: str, body: AssetActionRequest, principal: CurrentPrincipal = Depends(require_authenticated_principal)):
    tenant = principal.active_tenant_id
    permission = {
        "cancel_job": "ai_jobs.cancel",
        "reanalyze": "ai_analysis.run",
        "rebuild_projection": "search.rebuild",
        "reindex": "search.rebuild",
        "retry_failed_stage": "ai_jobs.retry",
    }[body.action]
    require_principal_permission(principal, permission)
    if body.force:
        require_principal_permission(principal, "ai_analysis.force")
    if body.force and not body.confirmed:
        raise HTTPException(422, "Forced re-analysis requires explicit confirmation")
    with SessionLocal() as session:
        asset = session.scalar(select(AssetModel).where(AssetModel.id == asset_id, AssetModel.tenant_id == tenant))
        if asset is None:
            raise HTTPException(404, "Asset not found")
        repository = ProcessingRepository(session)
        analyses = list(session.scalars(select(AssetAiAnalysisModel).where(AssetAiAnalysisModel.tenant_id == tenant, AssetAiAnalysisModel.asset_id == asset_id).order_by(AssetAiAnalysisModel.created_at.desc())))
        current = analyses[0] if analyses else None
        if body.action == "cancel_job":
            job = session.scalar(select(ProcessingJobModel).where(ProcessingJobModel.id == body.job_id, ProcessingJobModel.tenant_id == tenant))
            groups = related_ids(session, tenant, asset_id)
            if job is None or job.entity_id not in {asset_id, *groups[0], *groups[1], *groups[2]}:
                raise HTTPException(404, "Queued job not found")
            if job.status not in {"pending", "retry"}:
                raise HTTPException(409, "Only queued jobs can be cancelled")
            job.status = "failed"; job.last_error_code = "operator_cancelled"; job.last_error_message = "Cancelled by an authorized operator"; job.completed_at = datetime.now(timezone.utc)
            AiGovernanceRepository(session).event(tenant, "asset_admin_action", actor_id=principal.user_id, reason=body.reason, details={"asset_id": asset_id, "action": body.action, "job_id": job.id})
            session.commit()
            return {"action": body.action, "status": "cancelled", "job_id": job.id, "analysis_id": current.id if current else None}
        if body.action == "reanalyze":
            if current is None:
                raise HTTPException(409, "No metadata profile is available")
            settings = get_settings()
            if not (settings.DYNAMIC_AI_METADATA_ENABLED and settings.AI_SINGLE_ANALYSIS_ENABLED):
                raise HTTPException(409, "Single-asset analysis is disabled")
            analysis = AiMetadataRepository(session).create_analysis(tenant_id=tenant, asset_id=asset_id, metadata_profile_id=current.metadata_profile_id, prompt_version=current.prompt_version, pipeline_version=current.pipeline_version, ai_provider=current.ai_provider or "gemini", ai_model=current.ai_model or settings.GEMINI_MODEL, force=body.force)
            job = repository.create_job(tenant_id=tenant, job_type="asset_analyze", entity_type="asset_ai_analysis", entity_id=analysis.id, idempotency_key=f"asset-analyze:{analysis.id}", payload={"analysis_id": analysis.id, "asset_id": asset_id}, provider_key=analysis.ai_provider, provider_scope="ai")
            analysis_id = analysis.id
        elif body.action in {"rebuild_projection", "reindex"}:
            if current is None:
                raise HTTPException(409, "No analysis is available")
            job_type = "search_projection_build" if body.action == "rebuild_projection" else "asset_index"
            identity = current.projection_checksum or current.search_projection_version or current.id
            job = repository.create_job(tenant_id=tenant, job_type=job_type, entity_type="asset", entity_id=asset_id, idempotency_key=f"operator:{job_type}:{asset_id}:{identity}", payload={"asset_id": asset_id, "analysis_id": current.id, "direct_analysis": True}, provider_key="elasticsearch" if job_type == "asset_index" else None, provider_scope="search" if job_type == "asset_index" else None)
            analysis_id = current.id
        else:
            pipeline = session.scalar(select(AssetPipelineModel).where(AssetPipelineModel.tenant_id == tenant, AssetPipelineModel.asset_id == asset_id).order_by(AssetPipelineModel.updated_at.desc()))
            if pipeline is None or pipeline.state not in {state.value for state in FAILURE_STATES} or not pipeline.failure_retryable:
                raise HTTPException(409, "No retryable failed stage is available")
            retry_from = pipeline.state
            job_type = {"download_failed": "source_asset_download", "storage_failed": "asset_store", "analysis_failed": "asset_analyze", "projection_failed": "search_projection_build", "search_failed": "asset_index", "sidecar_failed": "metadata_sidecar_export"}[retry_from]
            pending_state = {"source_asset_download": "download_pending", "asset_store": "storage_pending", "asset_analyze": "analysis_pending", "search_projection_build": "projection_pending", "asset_index": "search_pending", "metadata_sidecar_export": "sidecar_pending"}[job_type]
            retry_key = f"operator-retry:{pipeline.id}:{retry_from}:{pipeline.updated_at.isoformat()}"
            AssetPipelineRepository(session).transition(pipeline, pending_state)
            providers = {"asset_store": ("google_drive", "storage"), "asset_analyze": ((current.ai_provider if current else None), "ai"), "asset_index": ("elasticsearch", "search"), "metadata_sidecar_export": ("google_drive", "storage")}
            provider = providers.get(job_type)
            job = repository.create_job(tenant_id=tenant, job_type=job_type, entity_type="asset_pipeline", entity_id=pipeline.id, idempotency_key=retry_key, payload={"pipeline_id": pipeline.id, "correlation_id": pipeline.correlation_id}, provider_key=provider[0] if provider else None, provider_scope=provider[1] if provider else None)
            analysis_id = pipeline.analysis_id
        AiGovernanceRepository(session).event(tenant, "asset_admin_action", actor_id=principal.user_id, reason=body.reason, details={"asset_id": asset_id, "action": body.action, "job_id": job.id})
        session.commit()
        return {"action": body.action, "status": "accepted", "job_id": job.id, "analysis_id": analysis_id}
