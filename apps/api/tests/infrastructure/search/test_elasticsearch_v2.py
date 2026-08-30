import json
import unittest

import httpx

from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config, ElasticsearchV3Index
from app.modules.search.index_types import SearchIndexDocument


class ElasticsearchV3MappingTest(unittest.TestCase):
    def test_mapping_is_strict_and_stable(self) -> None:
        definition = ElasticsearchV3Index.index_definition()
        properties = definition["mappings"]["properties"]
        self.assertEqual(definition["mappings"]["dynamic"], "strict")
        self.assertEqual(properties["facets"]["type"], "flattened")
        self.assertEqual(properties["path_values"]["type"], "nested")
        self.assertNotIn("metadata_json", properties)
        self.assertEqual(
            set(properties),
            {"asset_id", "tenant_id", "filename", "folder_path", "search_text", "search_terms", "normalized_terms", "phrases", "numbers", "facets", "path_values", "metadata_profile", "metadata_profile_version", "search_projection_version"},
        )

    def test_analyzer_normalizes_case_unicode_and_punctuation(self) -> None:
        analysis = ElasticsearchV3Index.index_definition()["settings"]["analysis"]
        analyzer = analysis["analyzer"]["cam_text_v2"]
        self.assertEqual(analyzer["char_filter"], ["cam_punctuation"])
        self.assertEqual(analyzer["filter"], ["lowercase", "asciifolding"])
    def test_v3_mapping_includes_searchable_visible_text_without_short_token_filter(self) -> None:
        definition = ElasticsearchV3Index(
            ElasticsearchV3Config("http://elastic.test", index_generation="v3")
        )._index_definition()
        properties = definition["mappings"]["properties"]
        self.assertEqual(properties["source_id"]["type"], "keyword")
        self.assertEqual(properties["visible_text"]["analyzer"], "cam_text_v2")
        self.assertEqual(properties["search_suggest"]["type"], "search_as_you_type")
        self.assertEqual(properties["filename"]["fields"]["normalized"]["type"], "keyword")
        self.assertNotIn("length", definition["settings"]["analysis"].get("filter", {}))


class ElasticsearchV3HttpIntegrationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if request.method == "HEAD":
                return httpx.Response(200)
            if request.url.path.startswith("/_alias/"):
                alias = request.url.path.rsplit("/", 1)[-1]
                value = (
                    {"is_write_index": True}
                    if alias.endswith("-write")
                    else {}
                )
                return httpx.Response(200, json={"creative-assets-v2-000001": {"aliases": {alias: value}}})
            if request.url.path == "/_bulk":
                return httpx.Response(200, json={"errors": False, "items": []})
            if request.method == "POST" and request.url.path.endswith("/_pit"):
                return httpx.Response(200, json={"id": "pit-opened"})
            if request.method == "DELETE" and request.url.path == "/_pit":
                return httpx.Response(200, json={"succeeded": True})
            if request.url.path.endswith("/_search"):
                return httpx.Response(200, json={"pit_id": "pit-refreshed", "hits": {"hits": []}})
            return httpx.Response(200, json={"acknowledged": True})

        self.client = httpx.AsyncClient(base_url="http://elastic.test", transport=httpx.MockTransport(handler))
        self.index = ElasticsearchV3Index(
            ElasticsearchV3Config("http://elastic.test", bulk_batch_size=1), client=self.client
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    @staticmethod
    def document(asset_id: str = "asset-1") -> SearchIndexDocument:
        return SearchIndexDocument(
            asset_id=asset_id, tenant_id="tenant-a", filename="cat.png",
            folder_path="animals", search_text="cat mama est 2015",
            search_terms=("cat", "mama", "est 2015"),
            normalized_terms=("cat", "mama", "est", "2015"),
            phrases=("est 2015",), numbers=("2015",),
            facets={"subject": ("cat",)},
            path_values=({"path": "subject", "value": "cat"},),
            metadata_profile="default", metadata_profile_version="1",
            search_projection_version="search-projection-v1",
        )

    async def test_create_uses_versioned_physical_name(self) -> None:
        name = await self.index.create_index("000002")
        self.assertEqual(name, "creative-assets-v2-000002")
        self.assertEqual(self.requests[-1].url.path, f"/{name}")

    async def test_bulk_uses_asset_id_and_doc_as_upsert(self) -> None:
        count = await self.index.bulk_upsert([self.document(), self.document("asset-2")])
        self.assertEqual(count, 2)
        bulk_requests = [item for item in self.requests if item.url.path == "/_bulk"]
        self.assertEqual(len(bulk_requests), 2)
        lines = bulk_requests[0].content.decode().splitlines()
        self.assertEqual(json.loads(lines[0])["update"]["_id"], "asset-1")
        self.assertTrue(json.loads(lines[1])["doc_as_upsert"])
        body = json.loads(lines[1])["doc"]
        self.assertNotIn("metadata_json", body)
        self.assertNotIn("visible_text", body)
        self.assertNotIn("search_suggest", body)

    async def test_single_asset_upsert_waits_for_refresh(self) -> None:
        await self.index.bulk_upsert([self.document()])
        request = [item for item in self.requests if item.url.path == "/_bulk"][-1]
        self.assertEqual(request.url.params.get("refresh"), "wait_for")


    async def test_v3_bulk_includes_v3_document_fields(self) -> None:
        index = ElasticsearchV3Index(
            ElasticsearchV3Config(
                "http://elastic.test", index_generation="v3", bulk_batch_size=1
            ),
            client=self.client,
        )
        document = SearchIndexDocument(
            asset_id="asset-1", tenant_id="tenant-a", source_id="source-1",
            filename="badge.jpg", folder_path="badges",
            visible_text=("BSN, RN",), search_suggest="bsn rn",
            search_text="bsn rn",
        )
        await index.bulk_upsert([document])
        request = [item for item in self.requests if item.url.path == "/_bulk"][-1]
        body = json.loads(request.content.decode().splitlines()[1])["doc"]
        self.assertEqual(body["source_id"], "source-1")
        self.assertEqual(body["visible_text"], ["BSN, RN"])
        self.assertEqual(body["search_suggest"], "bsn rn")


    async def test_bulk_can_target_new_physical_index_before_alias_switch(self) -> None:
        target = "creative-assets-v2-000002"
        count = await self.index.bulk_upsert_to_index([self.document()], target)
        self.assertEqual(count, 1)
        request = [item for item in self.requests if item.url.path == "/_bulk"][-1]
        action = json.loads(request.content.decode().splitlines()[0])
        self.assertEqual(action["update"]["_index"], target)
    async def test_alias_switch_is_atomic_and_returns_rollback_target(self) -> None:
        result = await self.index.switch_aliases("creative-assets-v2-000002")
        alias_requests = [item for item in self.requests if item.url.path == "/_aliases"]
        self.assertEqual(len(alias_requests), 1)
        actions = json.loads(alias_requests[0].content)["actions"]
        self.assertEqual(len(actions), 4)
        self.assertTrue(actions[-1]["add"]["is_write_index"])
        self.assertEqual(result.previous_read_indices, ("creative-assets-v2-000001",))
        await self.index.rollback_aliases(result.previous_read_indices[0])
        self.assertEqual(len([item for item in self.requests if item.url.path == "/_aliases"]), 2)

    async def test_pit_open_search_and_close_use_shared_abstraction(self) -> None:
        pit_id = await self.index.open_point_in_time(keep_alive="2m")
        self.assertEqual(pit_id, "pit-opened")
        self.assertEqual(
            self.requests[-1].url.path,
            "/creative-assets-v2-read/_pit",
        )
        result = await self.index.search_with_pit(
            {"query": {"match_all": {}}, "sort": [{"asset_id": "asc"}]},
            pit_id=pit_id,
            keep_alive="2m",
        )
        request = self.requests[-1]
        self.assertEqual(request.url.path, "/_search")
        body = json.loads(request.content)
        self.assertEqual(body["pit"], {"id": "pit-opened", "keep_alive": "2m"})
        self.assertEqual(result["pit_id"], "pit-refreshed")
        self.assertTrue(await self.index.close_point_in_time("pit-refreshed"))
        self.assertEqual(self.requests[-1].method, "DELETE")
        self.assertEqual(
            json.loads(self.requests[-1].content),
            {"id": "pit-refreshed"},
        )

    async def test_search_reads_through_read_alias(self) -> None:
        await self.index.search({"query": {"match_all": {}}})
        self.assertEqual(self.requests[-1].url.path, "/creative-assets-v2-read/_search")


if __name__ == "__main__":
    unittest.main()
