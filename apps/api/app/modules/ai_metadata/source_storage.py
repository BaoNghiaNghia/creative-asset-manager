from __future__ import annotations

from app.domain.providers.contracts import OpenStoredAssetInput, StoredAssetReadStream
from app.modules.pipeline.model import AssetPipelineModel


class PipelineSourceAssetStorage:
    """Read-only analysis input backed by the tenant-authorized source asset."""

    def __init__(self, resolver, *, tenant_id: str, pipeline: AssetPipelineModel):
        if pipeline.tenant_id != tenant_id or not pipeline.asset_id or not pipeline.source_asset_id:
            raise ValueError("tenant-scoped source pipeline identity is required")
        self.resolver = resolver
        self.tenant_id = tenant_id
        self.pipeline = pipeline

    async def open_asset(self, input: OpenStoredAssetInput) -> StoredAssetReadStream:
        if input.tenant_id != self.tenant_id or input.asset_id != self.pipeline.asset_id:
            raise ValueError("source analysis asset identity mismatch")
        context = self.resolver.open(tenant_id=self.tenant_id, pipeline=self.pipeline)
        stream = await context.__aenter__()
        closed = False

        async def close() -> None:
            nonlocal closed
            if closed:
                return
            closed = True
            await context.__aexit__(None, None, None)

        return StoredAssetReadStream(
            body=stream.body,
            close=close,
            content_type=stream.content_type,
            size_bytes=input.size_bytes,
        )
