from __future__ import annotations

import os
import unittest
from uuid import uuid4

from app.infrastructure.search.elasticsearch_v2 import (
    ElasticsearchV2Config,
    ElasticsearchV2Index,
)
from app.modules.search.index_types import SearchIndexDocument
from app.modules.search.query_builder import ElasticsearchQueryBuilder, SearchQueryConfig
from app.modules.search.query_parser import SearchQueryParser


ELASTICSEARCH_URL = os.getenv("INTEGRATION_ELASTICSEARCH_URL") or os.getenv(
    "ELASTICSEARCH_URL", ""
)


@unittest.skipUnless(ELASTICSEARCH_URL.startswith("http"), "Elasticsearch is not configured")
class ElasticsearchRealIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.prefix = f"cam-ci-{uuid4().hex[:12]}"
        self.index = ElasticsearchV2Index(
            ElasticsearchV2Config(
                ELASTICSEARCH_URL,
                index_prefix=self.prefix,
                bulk_batch_size=2,
            )
        )
        self.first = await self.index.create_index("000001")
        await self.index.switch_aliases(self.first)

    async def asyncTearDown(self) -> None:
        try:
            await self.index.client.delete(
                f"/{self.prefix}-v2-*",
                params={"expand_wildcards": "all"},
            )
        finally:
            await self.index.client.aclose()

    @staticmethod
    def document(
        asset_id: str,
        *,
        tenant_id: str = "tenant-a",
        text: str,
        subject: str,
        filename: str,
    ) -> SearchIndexDocument:
        normalized = tuple(text.split())
        return SearchIndexDocument(
            asset_id=asset_id,
            tenant_id=tenant_id,
            filename=filename,
            folder_path=f"campaigns/{subject}",
            search_text=text,
            search_terms=normalized,
            normalized_terms=normalized,
            phrases=("est 2015",) if "est 2015" in text else (),
            numbers=("2015",) if "2015" in text else (),
            facets={"subject": (subject,)},
            path_values=({"path": "subject", "value": subject},),
            metadata_profile="integration",
            metadata_profile_version="1",
            search_projection_version="search-projection-v1",
        )

    async def refresh(self) -> None:
        response = await self.index.client.post(f"/{self.index.write_alias}/_refresh")
        response.raise_for_status()

    async def search_ids(self, raw: str) -> set[str]:
        parsed = SearchQueryParser().parse(raw)
        body = ElasticsearchQueryBuilder().build(
            parsed,
            tenant_id="tenant-a",
            config=SearchQueryConfig(
                facet_names=frozenset({"subject"}),
                path_aliases={"subject": "subject"},
            ),
        )
        payload = await self.index.search(body)
        return {hit["_id"] for hit in payload["hits"]["hits"]}

    async def test_real_mapping_bulk_alias_queries_rollback_and_cleanup(self) -> None:
        mapping = await self.index.client.get(f"/{self.first}/_mapping")
        mapping.raise_for_status()
        self.assertEqual(
            mapping.json()[self.first]["mappings"]["dynamic"],
            "strict",
        )
        documents = [
            self.document(
                "cat-1",
                text="cat mama est 2015",
                subject="cat",
                filename="Cat Mama 2015.png",
            ),
            self.document(
                "cat-2",
                text="cat studio portrait",
                subject="cat",
                filename="Cat portrait.png",
            ),
            self.document(
                "dog-1",
                text="dog est 2015",
                subject="dog",
                filename="Dog 2015.png",
            ),
        ]
        self.assertEqual(await self.index.bulk_upsert(documents), 3)
        await self.refresh()

        invalid = await self.index.client.post(
            f"/{self.index.write_alias}/_doc/strict-mapping",
            json={
                **documents[0].to_document(),
                "asset_id": "strict-mapping",
                "unexpected_metadata_key": "must fail",
            },
        )
        self.assertEqual(invalid.status_code, 400)

        self.assertEqual(await self.search_ids("cat"), {"cat-1", "cat-2"})
        self.assertEqual(await self.search_ids("cat mama"), {"cat-1", "cat-2"})
        self.assertEqual(await self.search_ids("cat, est, 2015"), {"cat-1"})
        self.assertEqual(await self.search_ids('"est 2015"'), {"cat-1", "dog-1"})
        self.assertEqual(await self.search_ids("cat OR dog"), {"cat-1", "cat-2", "dog-1"})
        self.assertEqual(await self.search_ids("subject:cat"), {"cat-1", "cat-2"})

        second = await self.index.create_index("000002")
        switched = await self.index.switch_aliases(second)
        self.assertEqual(switched.previous_read_indices, (self.first,))
        rolled_back = await self.index.rollback_aliases(self.first)
        self.assertEqual(rolled_back.previous_read_indices, (second,))

        deleted = await self.index.client.delete(f"/{second}")
        deleted.raise_for_status()
        missing = await self.index.client.get(f"/{second}")
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
