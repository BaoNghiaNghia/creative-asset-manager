from __future__ import annotations

from app.modules.visual_search.contracts import VisualEmbedding
from app.modules.visual_search.elasticsearch import VisualIndexDocument, VisualMetadataFilters, VisualSearchElasticsearchIndex, VisualSearchHit, VisualSearchScope


class VisualSearchRepository:
    """Small persistence boundary; authorization remains with the caller."""

    def __init__(self, index: VisualSearchElasticsearchIndex) -> None:
        self._index = index

    async def upsert(self, document: VisualIndexDocument) -> None:
        await self._index.upsert(document)

    async def delete_asset(self, *, scope: VisualSearchScope, asset_id: str) -> int:
        return await self._index.delete_asset(tenant_id=scope.tenant_id, asset_id=asset_id)

    async def nearest(self, embedding: VisualEmbedding, *, scope: VisualSearchScope, metadata_filters: VisualMetadataFilters | None = None, limit: int = 40) -> list[VisualSearchHit]:
        return await self._index.search(embedding, scope=scope, metadata_filters=metadata_filters, limit=limit)
