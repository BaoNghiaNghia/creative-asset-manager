from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.domain.assets.hashing import sha256_stream
from app.modules.assets.model import AssetModel, AssetSourceLinkModel
from app.modules.assets.repository import (
    AssetContentConflictError,
    AssetRegistryRepository,
)


class ContentDeduplicationDisabledError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ContentDeduplicationResult:
    asset: AssetModel
    link: AssetSourceLinkModel | None
    reused_asset: bool
    reused_provider_version: bool
    bytes_hashed: int


class ContentDeduplicationService:
    def __init__(self, repository: AssetRegistryRepository, *, enabled: bool):
        self.repository = repository
        self.enabled = enabled

    async def ingest(
        self,
        *,
        tenant_id: str,
        source_asset_id: str,
        content_stream: AsyncIterator[bytes] | None,
        mime_type: str | None = None,
        provider_checksum: str | None = None,
        provider_version: str | None = None,
    ) -> ContentDeduplicationResult:
        if not self.enabled:
            raise ContentDeduplicationDisabledError(
                "CONTENT_DEDUP_ENABLED is disabled"
            )

        source_asset = self.repository.get_source_asset(tenant_id, source_asset_id)
        if source_asset is None:
            raise LookupError(source_asset_id)

        linked_asset = self.repository.find_linked_asset(tenant_id, source_asset_id)
        unchanged_checksum = bool(
            provider_checksum
            and source_asset.hashed_provider_checksum == provider_checksum
        )
        unchanged_version = bool(
            provider_version
            and source_asset.hashed_provider_version == provider_version
        )
        if linked_asset is not None and (unchanged_checksum or unchanged_version):
            return ContentDeduplicationResult(
                asset=linked_asset,
                link=None,
                reused_asset=True,
                reused_provider_version=True,
                bytes_hashed=0,
            )

        if content_stream is None:
            raise ValueError("content stream is required when provider version cannot be reused")

        hash_result = await sha256_stream(content_stream)
        asset = self.repository.find_asset_by_content_hash(
            tenant_id, hash_result.content_hash
        )
        reused_asset = asset is not None
        if asset is None:
            try:
                asset = self.repository.create_asset(
                    tenant_id=tenant_id,
                    content_hash=hash_result.content_hash,
                    mime_type=mime_type or source_asset.mime_type,
                    size_bytes=hash_result.size_bytes,
                )
            except AssetContentConflictError:
                asset = self.repository.find_asset_by_content_hash(
                    tenant_id, hash_result.content_hash
                )
                if asset is None:
                    raise
                reused_asset = True

        link = self.repository.link_source_asset(
            tenant_id=tenant_id,
            asset_id=asset.id,
            source_asset_id=source_asset_id,
        )
        self.repository.mark_source_asset_hashed_version(
            tenant_id=tenant_id,
            source_asset_id=source_asset_id,
            provider_checksum=provider_checksum,
            provider_version=provider_version,
        )
        self.repository.commit()
        return ContentDeduplicationResult(
            asset=asset,
            link=link,
            reused_asset=reused_asset,
            reused_provider_version=False,
            bytes_hashed=hash_result.size_bytes,
        )
