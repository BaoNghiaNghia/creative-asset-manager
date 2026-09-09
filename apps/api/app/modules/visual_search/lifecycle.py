from __future__ import annotations

from app.core.config import Settings
from app.modules.processing.repository import ProcessingRepository
from app.modules.visual_search.encoder import SIGLIP_BASELINE_PREPROCESS_VERSION

VISUAL_EMBEDDING_SCHEMA_VERSION = "visual_embedding_v1"


def visual_index_job_enabled(settings: Settings | None) -> bool:
    if settings is None:
        return False
    return bool(
        settings.PROCESSING_JOBS_ENABLED
        and settings.VISUAL_SEARCH_ENABLED
        and settings.ELASTICSEARCH_URL
    )


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
) -> bool:
    """Enqueue one versioned, idempotent visual projection job.

    The caller must already be inside the asset/source transaction.  The worker
    rechecks the immutable content hash, so metadata-only source updates do not
    cause a second encode and obsolete jobs cannot index replacement content.
    """
    if not visual_index_job_enabled(settings):
        return False
    key = visual_index_job_key(asset_id, content_sha256)
    before = processing.get_job_by_key(tenant_id, key)
    processing.create_job(
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
            "preprocess_version": SIGLIP_BASELINE_PREPROCESS_VERSION,
        },
        provider_key="visual_encoder",
        provider_scope="visual",
    )
    return before is None



def enqueue_visual_retire_sync(
    processing: ProcessingRepository,
    *,
    settings: Settings | None,
    tenant_id: str,
    asset_id: str,
    identity: str,
) -> bool:
    """Reconcile a retired source without assuming it was the asset's only link."""
    if not visual_index_job_enabled(settings):
        return False
    key = f"visual-retire:{asset_id}:{identity}:{VISUAL_EMBEDDING_SCHEMA_VERSION}"
    before = processing.get_job_by_key(tenant_id, key)
    processing.create_job(
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
    )
    return before is None
