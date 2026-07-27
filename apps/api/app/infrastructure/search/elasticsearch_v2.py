from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import httpx

from app.modules.search.index_types import AliasSwitchResult, SearchIndexDocument

_VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


class ElasticsearchV2RequestError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ElasticsearchV2Config:
    base_url: str
    index_prefix: str = "creative-assets"
    request_timeout_seconds: float = 10.0
    bulk_batch_size: int = 500
    index_generation: str = "v2"

    def __post_init__(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must be an HTTP(S) URL")
        if not _VERSION_RE.fullmatch(self.index_prefix):
            raise ValueError("invalid index_prefix")
        if self.request_timeout_seconds <= 0 or self.bulk_batch_size < 1:
            raise ValueError("invalid Elasticsearch limits")
        if self.index_generation not in {"v2", "v3"}:
            raise ValueError("index_generation must be v2 or v3")


class ElasticsearchV2Index:
    def __init__(
        self,
        config: ElasticsearchV2Config,
        *,
        client: httpx.AsyncClient | None = None,
    ):
        self.config = config
        self.read_alias = f"{config.index_prefix}-{config.index_generation}-read"
        self.write_alias = f"{config.index_prefix}-{config.index_generation}-write"
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"), timeout=config.request_timeout_seconds
        )

    async def __aenter__(self) -> "ElasticsearchV2Index":
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client:
            await self.client.aclose()

    def physical_index_name(self, version: str) -> str:
        normalized = version.strip().lower()
        if not _VERSION_RE.fullmatch(normalized):
            raise ValueError("invalid index version")
        if len(f"{self.config.index_prefix}-{self.config.index_generation}-{normalized}") > 255:
            raise ValueError("physical index name exceeds Elasticsearch limit")
        return f"{self.config.index_prefix}-{self.config.index_generation}-{normalized}"

    @staticmethod
    def index_definition() -> dict[str, Any]:
        return {
            "settings": {
                "analysis": {
                    "char_filter": {
                        "cam_punctuation": {
                            "type": "pattern_replace",
                            "pattern": "[\\p{P}\\p{S}]+",
                            "replacement": " ",
                        }
                    },
                    "analyzer": {
                        "cam_text_v2": {
                            "type": "custom",
                            "char_filter": ["cam_punctuation"],
                            "tokenizer": "standard",
                            "filter": ["lowercase", "asciifolding"],
                        }
                    },
                }
            },
            "mappings": {
                "dynamic": "strict",
                "properties": {
                    "asset_id": {"type": "keyword"},
                    "tenant_id": {"type": "keyword"},
                    "filename": {"type": "text", "analyzer": "cam_text_v2"},
                    "folder_path": {"type": "text", "analyzer": "cam_text_v2"},
                    "search_text": {"type": "text", "analyzer": "cam_text_v2"},
                    "search_terms": {"type": "keyword"},
                    "normalized_terms": {"type": "keyword"},
                    "phrases": {"type": "keyword"},
                    "numbers": {"type": "keyword"},
                    "facets": {"type": "flattened"},
                    "path_values": {
                        "type": "nested",
                        "properties": {
                            "path": {"type": "keyword"},
                            "value": {"type": "keyword"},
                        },
                    },
                    "metadata_profile": {"type": "keyword"},
                    "metadata_profile_version": {"type": "keyword"},
                    "search_projection_version": {"type": "keyword"},
                },
            },
        }

    def _index_definition(self) -> dict[str, Any]:
        definition = copy.deepcopy(self.index_definition())
        if self.config.index_generation != "v3":
            return definition
        analysis = definition["settings"]["analysis"]
        analysis["normalizer"] = {
            "cam_keyword": {"type": "custom", "filter": ["lowercase", "asciifolding"]}
        }
        properties = definition["mappings"]["properties"]
        properties["source_id"] = {"type": "keyword"}
        properties["filename"] = {
            "type": "text",
            "analyzer": "cam_text_v2",
            "fields": {"normalized": {"type": "keyword", "normalizer": "cam_keyword"}},
        }
        properties["visible_text"] = {"type": "text", "analyzer": "cam_text_v2"}
        properties["search_suggest"] = {
            "type": "search_as_you_type", "analyzer": "cam_text_v2"
        }
        return definition
    async def create_index(self, version: str) -> str:
        index_name = self.physical_index_name(version)
        await self._request("PUT", f"/{index_name}", json_body=self._index_definition())
        return index_name

    async def bulk_upsert(self, documents: Sequence[SearchIndexDocument]) -> int:
        return await self.bulk_upsert_to_index(documents, self.write_alias)

    async def bulk_upsert_to_index(
        self,
        documents: Sequence[SearchIndexDocument],
        target_index: str,
    ) -> int:
        if target_index != self.write_alias and (
            not _VERSION_RE.fullmatch(target_index)
            or not target_index.startswith(f"{self.config.index_prefix}-{self.config.index_generation}-")
        ):
            raise ValueError("invalid bulk target index")
        count = 0
        for start in range(0, len(documents), self.config.bulk_batch_size):
            batch = documents[start : start + self.config.bulk_batch_size]
            lines: list[str] = []
            for document in batch:
                lines.append(
                    json.dumps(
                        {
                            "update": {
                                "_index": target_index,
                                "_id": document.asset_id,
                            }
                        },
                        separators=(",", ":"),
                    )
                )
                lines.append(
                    json.dumps(
                        {
                            "doc": self._document_body(document),
                            "doc_as_upsert": True,
                        },
                        separators=(",", ":"),
                    )
                )
            if not lines:
                continue
            response = await self._request(
                "POST",
                "/_bulk?refresh=wait_for" if len(documents) == 1 else "/_bulk",
                content=("\n".join(lines) + "\n").encode(),
                headers={"Content-Type": "application/x-ndjson"},
            )
            if response.get("errors"):
                failures = [
                    item
                    for item in response.get("items", [])
                    if next(iter(item.values())).get("error")
                ]
                raise ElasticsearchV2RequestError(
                    f"bulk indexing failed for {len(failures)} item(s)"
                )
            count += len(batch)
        return count

    def _document_body(self, document: SearchIndexDocument) -> dict[str, Any]:
        body = document.to_document()
        if self.config.index_generation == "v2":
            for field in ("source_id", "visible_text", "search_suggest"):
                body.pop(field, None)
        return body
    async def switch_aliases(self, target_index: str) -> AliasSwitchResult:
        if not _VERSION_RE.fullmatch(target_index) or not target_index.startswith(
            f"{self.config.index_prefix}-{self.config.index_generation}-"
        ):
            raise ValueError("invalid target index")
        await self._request("HEAD", f"/{target_index}")
        current = await self._alias_indices()
        actions: list[dict[str, Any]] = []
        for index in sorted(current["read"]):
            actions.append({"remove": {"index": index, "alias": self.read_alias, "must_exist": True}})
        for index in sorted(current["write"]):
            actions.append({"remove": {"index": index, "alias": self.write_alias, "must_exist": True}})
        actions.extend(
            [
                {"add": {"index": target_index, "alias": self.read_alias}},
                {"add": {"index": target_index, "alias": self.write_alias, "is_write_index": True}},
            ]
        )
        await self._request("POST", "/_aliases", json_body={"actions": actions})
        return AliasSwitchResult(
            target_index,
            tuple(sorted(current["read"])),
            tuple(sorted(current["write"])),
        )

    async def rollback_aliases(self, previous_index: str) -> AliasSwitchResult:
        return await self.switch_aliases(previous_index)

    async def alias_indices(self) -> dict[str, set[str]]:
        return await self._alias_indices()

    async def index_count(self, index_name: str) -> int:
        response = await self._request("GET", f"/{index_name}/_count")
        return int(response.get("count", 0))

    async def index_mapping(self, index_name: str) -> dict[str, Any]:
        return await self._request("GET", f"/{index_name}/_mapping")

    async def index_settings(self, index_name: str) -> dict[str, Any]:
        return await self._request("GET", f"/{index_name}/_settings")

    async def verification_search(
        self, index_name: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        sanitized = {key: value for key, value in body.items() if not key.startswith("_")}
        return await self._request("POST", f"/{index_name}/_search", json_body=sanitized)

    async def delete_index(self, index_name: str) -> None:
        await self._request("DELETE", f"/{index_name}")

    async def search(self, body: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._request("POST", f"/{self.read_alias}/_search", json_body=body)

    async def _alias_indices(self) -> dict[str, set[str]]:
        responses = [
            await self._request("GET", f"/_alias/{alias}", allow_not_found=True)
            for alias in (self.read_alias, self.write_alias)
        ]
        result = {"read": set(), "write": set()}
        for response in responses:
            for index, value in response.items():
                aliases = value.get("aliases", {})
                if self.read_alias in aliases:
                    result["read"].add(index)
                if self.write_alias in aliases:
                    result["write"].add(index)
        return result

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        allow_not_found: bool = False,
    ) -> dict[str, Any]:
        response = await self.client.request(
            method, path, json=json_body, content=content, headers=headers
        )
        if allow_not_found and response.status_code == 404:
            return {}
        if method == "HEAD" and response.is_success:
            return {}
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ElasticsearchV2RequestError(
                f"Elasticsearch {method} {path} returned {response.status_code}"
            ) from exc
        if not response.content:
            return {}
        payload = response.json()
        if not isinstance(payload, dict):
            raise ElasticsearchV2RequestError("Elasticsearch returned a non-object response")
        return payload
