from __future__ import annotations

from app.core.config import Settings
from app.modules.processing.repository import ProcessingRepository
from app.modules.visual_search.eligibility import visual_search_infrastructure_enabled, visual_search_tenant_eligible
from app.modules.visual_search.model_spec import (
    SIGLIP2_PREPROCESS_VERSION,
    VISUAL_SEARCH_ACTIVE_DESCRIPTOR,
)

VISUAL_EMBEDDING_SCHEMA_VERSION = VISUAL_SEARCH_ACTIVE_DESCRIPTOR.embedding_schema_version
VISUAL_INDEX_SYNC_PRIORITY = 20


def visual_index_job_enabled(settings: Settings | None, tenant_id: str) -> bool:
    if settings is None:
        return False
    return visual_search_infrastructure_enabled(settings) and visual_search_tenant_eligible(settings, tenant_id)


def visual_index_job_key(asset_id: str, content_sha256: str) -> str:
    return f"visual-index:{asset_id}:{content_sha256}:{VISUAL_EMBEDDING_SCHEMA_VERSION}"


def enqueue_visual_index_sync(
    processing: ProcessingRepository,
    *,
    settings: Settings | None,
    tenant_id: str,
    asset_id: str,
    source_asset_id: str,
    content_sha256: str,
    priority: int = VISUAL_INDEX_SYNC_PRIORITY,
) -> bool:
    """Enqueue one versioned, idempotent visual projection job.

    The caller must already be inside the asset/source transaction.  The worker
    rechecks the immutable content hash, so metadata-only source updates do not
    cause a second encode and obsolete jobs cannot index replacement content.
    """
    if not visual_index_job_enabled(settings, tenant_id):
        return False
    if priority < 0:
        raise ValueError("visual index priority cannot be negative")
    key = visual_index_job_key(asset_id, content_sha256)
    _job, created = processing.create_job_once(
        tenant_id=tenant_id,
        job_type="visual_index_sync",
        entity_type="asset",
        entity_id=asset_id,
        idempotency_key=key,
        payload={
            "asset_id": asset_id,
            "source_asset_id": source_asset_id,
            "content_sha256": content_sha256,
            "embedding_schema_version": VISUAL_EMBEDDING_SCHEMA_VERSION,
            "preprocess_version": SIGLIP2_PREPROCESS_VERSION,
        },
        provider_key="visual_encoder",
        provider_scope="visual",
        priority=priority,
    )
    return created



def enqueue_visual_retire_sync(
    processing: ProcessingRepository,
    *,
    settings: Settings | None,
    tenant_id: str,
    asset_id: str,
    identity: str,
) -> bool:
    """Reconcile a retired source without assuming it was the asset's only link."""
    if not visual_search_infrastructure_enabled(settings):
        return False
    key = f"visual-retire:{asset_id}:{identity}:{VISUAL_EMBEDDING_SCHEMA_VERSION}"
    _job, created = processing.create_job_once(
        tenant_id=tenant_id,
        job_type="visual_index_sync",
        entity_type="asset",
        entity_id=asset_id,
        idempotency_key=key,
        payload={
            "asset_id": asset_id,
            "operation": "reconcile_retired_source",
            "embedding_schema_version": VISUAL_EMBEDDING_SCHEMA_VERSION,
        },
        provider_key="visual_encoder",
        provider_scope="visual",
        priority=VISUAL_INDEX_SYNC_PRIORITY,
    )
    return created