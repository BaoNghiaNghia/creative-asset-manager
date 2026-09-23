from __future__ import annotations

import re

from app.modules.assets.model import AssetModel, SourceAssetModel
from app.modules.assets.repository import (
    AssetContentConflictError,
    AssetRegistryRepository,
)


_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def normalize_sha256(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized if _SHA256_RE.fullmatch(normalized) else None


def ensure_source_asset_link(
    repository: AssetRegistryRepository,
    *,
    source_asset: SourceAssetModel,
    content_hash: str,
) -> AssetModel:
    """Ensure one source item resolves to the tenant content-addressed Asset.

    The caller must provide the actual SHA-256 digest of the source bytes (or a
    provider-supplied SHA-256 checksum). The operation is idempotent and safely
    relinks a source when its content changes.
    """
    normalized = normalize_sha256(content_hash)
    if normalized is None:
        raise ValueError("content_hash must be a lowercase SHA-256 digest")
    if not source_asset.tenant_id:
        raise ValueError("source asset must be tenant scoped")

    linked = repository.find_linked_asset(
        source_asset.tenant_id,
        source_asset.id,
    )
    if linked is not None and linked.content_hash == normalized:
        repository.mark_source_asset_hashed_version(
            tenant_id=source_asset.tenant_id,
            source_asset_id=source_asset.id,
            provider_checksum=source_asset.provider_checksum,
            provider_version=source_asset.provider_version,
        )
        return linked

    asset = repository.find_asset_by_content_hash(
        source_asset.tenant_id,
        normalized,
    )
    if asset is None:
        try:
            asset = repository.create_asset(
                tenant_id=source_asset.tenant_id,
                content_hash=normalized,
                mime_type=source_asset.mime_type,
                size_bytes=source_asset.size_bytes,
            )
        except AssetContentConflictError:
            asset = repository.find_asset_by_content_hash(
                source_asset.tenant_id,
                normalized,
            )
            if asset is None:
                raise

    repository.link_source_asset(
        tenant_id=source_asset.tenant_id,
        asset_id=asset.id,
        source_asset_id=source_asset.id,
    )
    repository.mark_source_asset_hashed_version(
        tenant_id=source_asset.tenant_id,
        source_asset_id=source_asset.id,
        provider_checksum=source_asset.provider_checksum,
        provider_version=source_asset.provider_version,
    )
    return asset
