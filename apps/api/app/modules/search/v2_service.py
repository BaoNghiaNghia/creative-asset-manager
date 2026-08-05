from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.modules.search.index_types import SearchIndexDocument, SearchIndexProvider
from app.modules.search.query_builder import ElasticsearchQueryBuilder, SearchQueryConfig
from app.modules.search.query_parser import SearchQueryParser


class ElasticsearchV3DisabledError(RuntimeError):
    pass


class ElasticsearchV3Service:
    def __init__(
        self,
        provider: SearchIndexProvider,
        *,
        index_enabled: bool = False,
        parser_enabled: bool = False,
        parser: SearchQueryParser | None = None,
        query_builder: ElasticsearchQueryBuilder | None = None,
    ):
        self.provider = provider
        self.index_enabled = index_enabled
        self.parser_enabled = parser_enabled
        self.parser = parser or SearchQueryParser()
        self.query_builder = query_builder or ElasticsearchQueryBuilder()

    async def bulk_upsert(self, documents: Sequence[SearchIndexDocument]) -> int:
        self._require_index()
        return await self.provider.bulk_upsert(documents)

    async def search(
        self,
        raw_query: str,
        *,
        tenant_id: str,
        config: SearchQueryConfig | None = None,
        size: int = 50,
        offset: int = 0,
    ) -> Mapping[str, Any]:
        self._require_index()
        if not self.parser_enabled:
            raise ElasticsearchV3DisabledError("SEARCH_QUERY_PARSER_V2_ENABLED is false")
        parsed = self.parser.parse(raw_query)
        body = self.query_builder.build(
            parsed, tenant_id=tenant_id, config=config, size=size, offset=offset
        )
        return await self.provider.search(body)

    def _require_index(self) -> None:
        if not self.index_enabled:
            raise ElasticsearchV3DisabledError("ELASTICSEARCH_V2_ENABLED is false")
