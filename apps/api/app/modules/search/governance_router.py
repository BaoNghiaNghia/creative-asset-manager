from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV2Config, ElasticsearchV2Index
from app.modules.processing_policy.auth import ProcessingAdmin, require_processing_admin
from app.modules.search.active_analysis import ActiveAnalysisService, AnalysisActivationError
from app.modules.search.governance_model import (
    ActiveAnalysisAuditModel, SearchIndexRecordModel, TenantSearchShadowPolicyModel,
)
from app.modules.search.index_lifecycle import IndexVerificationError, SearchIndexLifecycleService, VerificationSpec
from app.modules.search.shadow import SearchShadowRepository

router = APIRouter(prefix="/api/v1/admin/search", tags=["search-governance"])


class ActivationRequest(BaseModel):
    analysis_id: str
    reason: str | None = Field(None, max_length=1000)
    search_context: str = Field("search_v2", min_length=1, max_length=64)
    rebuild_and_reindex: bool = False


class RollbackRequest(BaseModel):
    reason: str | None = Field(None, max_length=1000)
    search_context: str = Field("search_v2", min_length=1, max_length=64)
    rebuild_and_reindex: bool = True


class ShadowPolicyRequest(BaseModel):
    enabled: bool = False
    emergency_disabled: bool = False
    primary_version: str = "v1"
    shadow_version: str = "v2"
    sample_percentage: int = Field(0, ge=0, le=100)
    timeout_ms: int = Field(250, gt=0, le=10000)
    persist_raw_query: bool = False
    raw_query_retention_days: int = Field(1, gt=0)
    report_retention_days: int = Field(30, gt=0)
    top_k: int = Field(10, ge=1, le=100)


class VerifyIndexRequest(BaseModel):
    expected_projection_version: str
    minimum_document_count: int = Field(1, ge=0)
    maximum_indexing_failures: int = Field(0, ge=0)


class CleanupIndexRequest(BaseModel):
    min_age_hours: int = Field(24, gt=0)
    preserve_previous: int = Field(1, ge=1)
    limit: int = Field(20, ge=1, le=100)
    dry_run: bool = True
    confirmed: bool = False


def _authorize(admin: ProcessingAdmin, tenant_id: str) -> None:
    admin.authorize_tenant(tenant_id)


@router.get("/tenants/{tenant}/assets/{asset_id}/active-analysis")
def active_analysis(tenant: str, asset_id: str, admin: ProcessingAdmin = Depends(require_processing_admin)):
    _authorize(admin, tenant)
    with SessionLocal() as session:
        rows = list(session.scalars(select(ActiveAnalysisAuditModel).where(
            ActiveAnalysisAuditModel.tenant_id == tenant,
            ActiveAnalysisAuditModel.asset_id == asset_id,
        ).order_by(ActiveAnalysisAuditModel.created_at.desc()).limit(100)))
        return {"tenant_id": tenant, "asset_id": asset_id, "history": [{
            "analysis_id": row.analysis_id, "previous_analysis_id": row.previous_analysis_id,
            "action": row.action, "actor_id": row.actor_id, "reason": row.reason,
            "search_context": row.search_context, "created_at": row.created_at,
        } for row in rows]}


@router.post("/tenants/{tenant}/assets/{asset_id}/active-analysis")
def activate_analysis(tenant: str, asset_id: str, body: ActivationRequest, admin: ProcessingAdmin = Depends(require_processing_admin)):
    _authorize(admin, tenant)
    settings = get_settings()
    if not settings.DETERMINISTIC_ACTIVE_ANALYSIS_ENABLED:
        raise HTTPException(409, "Deterministic active analysis is disabled")
    with SessionLocal() as session:
        try:
            service = ActiveAnalysisService(session)
            result = service.activate(
                tenant_id=tenant, asset_id=asset_id, analysis_id=body.analysis_id,
                actor_id=admin.actor_id, reason=body.reason, search_context=body.search_context,
            )
            jobs = service.enqueue_rebuild_and_reindex(tenant_id=tenant, active=result.active) if body.rebuild_and_reindex else None
            session.commit()
            return {"analysis_id": result.active.analysis_id, "previous_analysis_id": result.previous_analysis_id, "jobs": jobs}
        except (LookupError, AnalysisActivationError) as exc:
            session.rollback()
            raise HTTPException(409, str(exc)) from exc


@router.post("/tenants/{tenant}/assets/{asset_id}/active-analysis/rollback")
def rollback_analysis(tenant: str, asset_id: str, body: RollbackRequest, admin: ProcessingAdmin = Depends(require_processing_admin)):
    _authorize(admin, tenant)
    if not get_settings().DETERMINISTIC_ACTIVE_ANALYSIS_ENABLED:
        raise HTTPException(409, "Deterministic active analysis is disabled")
    with SessionLocal() as session:
        try:
            service = ActiveAnalysisService(session)
            result = service.rollback(
                tenant_id=tenant, asset_id=asset_id, actor_id=admin.actor_id,
                reason=body.reason, search_context=body.search_context,
            )
            jobs = service.enqueue_rebuild_and_reindex(tenant_id=tenant, active=result.active) if body.rebuild_and_reindex else None
            session.commit()
            return {"analysis_id": result.active.analysis_id, "previous_analysis_id": result.previous_analysis_id, "jobs": jobs}
        except (LookupError, AnalysisActivationError) as exc:
            session.rollback()
            raise HTTPException(409, str(exc)) from exc


@router.get("/tenants/{tenant}/shadow-policy")
def get_shadow_policy(tenant: str, admin: ProcessingAdmin = Depends(require_processing_admin)):
    _authorize(admin, tenant)
    settings = get_settings()
    with SessionLocal() as session:
        effective = SearchShadowRepository(session).effective_policy(
            tenant, global_enabled=settings.SEARCH_SHADOW_COMPARISON_ENABLED,
            max_timeout_ms=settings.SEARCH_SHADOW_MAX_TIMEOUT_MS,
        )
        row = session.get(TenantSearchShadowPolicyModel, tenant)
        return {"configured": row is not None, "effective": effective.__dict__ if hasattr(effective, "__dict__") else {
            key: getattr(effective, key) for key in effective.__dataclass_fields__
        }}


@router.put("/tenants/{tenant}/shadow-policy")
def update_shadow_policy(tenant: str, body: ShadowPolicyRequest, admin: ProcessingAdmin = Depends(require_processing_admin)):
    _authorize(admin, tenant)
    if body.primary_version not in {"v1", "v2"} or body.shadow_version not in {"v1", "v2"} or body.primary_version == body.shadow_version:
        raise HTTPException(422, "Primary and shadow must be distinct v1/v2 versions")
    with SessionLocal() as session:
        row = session.get(TenantSearchShadowPolicyModel, tenant) or TenantSearchShadowPolicyModel(tenant_id=tenant)
        for key, value in body.model_dump().items():
            setattr(row, key, value)
        row.updated_by = admin.actor_id
        row.updated_at = datetime.now(timezone.utc)
        session.add(row)
        session.commit()
        return {"tenant_id": tenant, **body.model_dump()}


@router.get("/tenants/{tenant}/shadow-report")
def shadow_report(tenant: str, started_at: datetime | None = None, ended_at: datetime | None = None, query_type: str | None = None, admin: ProcessingAdmin = Depends(require_processing_admin)):
    _authorize(admin, tenant)
    with SessionLocal() as session:
        return SearchShadowRepository(session).report(tenant, started_at=started_at, ended_at=ended_at, query_type=query_type)


@router.post("/indices/{record_id}/verify")
async def verify_index(record_id: str, body: VerifyIndexRequest, admin: ProcessingAdmin = Depends(require_processing_admin)):
    settings = get_settings()
    if not settings.ELASTICSEARCH_INDEX_LIFECYCLE_ENABLED or not settings.ELASTICSEARCH_URL:
        raise HTTPException(409, "Index lifecycle operations are disabled")
    async with ElasticsearchV2Index(ElasticsearchV2Config(settings.ELASTICSEARCH_URL, settings.ELASTICSEARCH_INDEX_PREFIX)) as provider:
        with SessionLocal() as session:
            try:
                row = await SearchIndexLifecycleService(session, provider).verify(
                    record_id, VerificationSpec(
                        body.expected_projection_version, body.minimum_document_count,
                        body.maximum_indexing_failures,
                    ), actor_id=admin.actor_id,
                )
                session.commit()
                return {"index": row.physical_index_name, "verification": row.verification_json}
            except (LookupError, IndexVerificationError) as exc:
                session.rollback()
                raise HTTPException(409, str(exc)) from exc


@router.post("/indices/{record_id}/activate")
async def activate_index(record_id: str, admin: ProcessingAdmin = Depends(require_processing_admin)):
    settings = get_settings()
    if not settings.ELASTICSEARCH_INDEX_LIFECYCLE_ENABLED or not settings.ELASTICSEARCH_URL:
        raise HTTPException(409, "Index lifecycle operations are disabled")
    async with ElasticsearchV2Index(ElasticsearchV2Config(settings.ELASTICSEARCH_URL, settings.ELASTICSEARCH_INDEX_PREFIX)) as provider:
        with SessionLocal() as session:
            try:
                row = await SearchIndexLifecycleService(session, provider).activate(record_id, actor_id=admin.actor_id)
                session.commit()
                return {"index": row.physical_index_name, "state": row.lifecycle_state}
            except (LookupError, IndexVerificationError) as exc:
                session.rollback()
                raise HTTPException(409, str(exc)) from exc


@router.post("/indices/cleanup")
async def cleanup_indices(body: CleanupIndexRequest, admin: ProcessingAdmin = Depends(require_processing_admin)):
    settings = get_settings()
    if not settings.ELASTICSEARCH_INDEX_LIFECYCLE_ENABLED or not settings.ELASTICSEARCH_URL:
        raise HTTPException(409, "Index lifecycle operations are disabled")
    async with ElasticsearchV2Index(ElasticsearchV2Config(settings.ELASTICSEARCH_URL, settings.ELASTICSEARCH_INDEX_PREFIX)) as provider:
        with SessionLocal() as session:
            deleted = await SearchIndexLifecycleService(session, provider).cleanup(
                index_prefix=settings.ELASTICSEARCH_INDEX_PREFIX, actor_id=admin.actor_id,
                min_age=timedelta(hours=body.min_age_hours),
                preserve_previous=body.preserve_previous, limit=body.limit,
                dry_run=body.dry_run, confirmed=body.confirmed,
            )
            session.commit()
            return {"dry_run": body.dry_run, "indices": deleted}
