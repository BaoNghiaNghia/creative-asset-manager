from __future__ import annotations

import copy
from typing import Any, Mapping

from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config, ElasticsearchV3Index
from app.modules.video_search.indexing import video_index_mapping


class VideoSearchElasticsearchIndex:
    """Dedicated, versioned Elasticsearch index for canonical video documents."""

    def __init__(self, config: ElasticsearchV3Config):
        self._index = ElasticsearchV3Index(
            ElasticsearchV3Config(
                base_url=config.base_url, index_prefix=f"{config.index_prefix}-video",
                request_timeout_seconds=config.request_timeout_seconds,
                bulk_batch_size=config.bulk_batch_size, index_generation=config.index_generation,
            )
        )

    @property
    def read_alias(self) -> str: return self._index.read_alias
    @property
    def write_alias(self) -> str: return self._index.write_alias
    def physical_index_name(self, version: str) -> str: return self._index.physical_index_name(version)

    async def aclose(self) -> None: await self._index.aclose()

    @staticmethod
    def index_definition() -> dict[str, Any]:
        definition = copy.deepcopy(ElasticsearchV3Index.index_definition())
        definition["mappings"] = video_index_mapping()
        return definition

    async def create_index(self, version: str) -> str:
        name = self.physical_index_name(version)
        await self._index._request("PUT", f"/{name}", json_body=self.index_definition())
        return name

    async def ensure_index(self, version: str) -> str:
        name = self.physical_index_name(version)
        settings = await self._index._request(
            "GET", f"/{name}/_settings", allow_not_found=True
        )
        if not settings:
            await self.create_index(version)
        return name

    async def switch_aliases(self, target_index: str):
        return await self._index.switch_aliases(target_index)

    async def upsert_video_document(self, document: Mapping[str, Any]) -> None:
        identifier = document.get("_id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("video document requires deterministic _id")
        body = {key: value for key, value in document.items() if key != "_id"}
        await self._index._request(
            "PUT", f"/{self.write_alias}/_doc/{identifier}?refresh=wait_for",
            json_body=body,
        )

    async def get_document(self, document_id: str) -> Mapping[str, Any]:
        return await self._index._request("GET", f"/{self.read_alias}/_doc/{document_id}")

    async def search(self, body: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._index._request(
            "POST", f"/{self.read_alias}/_search", json_body=body
        )

    async def index_mapping(self, name: str) -> dict[str, Any]:
        return await self._index.index_mapping(name)

    async def index_count(self, name: str) -> int:
        return await self._index.index_count(name)
