import unittest

from app.modules.search.v2_service import ElasticsearchV2DisabledError, ElasticsearchV2Service


class FakeProvider:
    def __init__(self):
        self.body = None

    async def bulk_upsert(self, documents):
        return len(documents)

    async def search(self, body):
        self.body = body
        return {"hits": {"hits": []}}


class ElasticsearchV2ServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_flags_keep_v2_index_and_parser_disabled(self) -> None:
        service = ElasticsearchV2Service(FakeProvider())
        with self.assertRaises(ElasticsearchV2DisabledError):
            await service.bulk_upsert([])
        with self.assertRaises(ElasticsearchV2DisabledError):
            await service.search("cat", tenant_id="tenant-a")

    async def test_parser_has_independent_flag(self) -> None:
        service = ElasticsearchV2Service(FakeProvider(), index_enabled=True)
        with self.assertRaisesRegex(ElasticsearchV2DisabledError, "SEARCH_QUERY"):
            await service.search("cat", tenant_id="tenant-a")

    async def test_enabled_service_uses_provider(self) -> None:
        provider = FakeProvider()
        service = ElasticsearchV2Service(provider, index_enabled=True, parser_enabled=True)
        result = await service.search("cat", tenant_id="tenant-a")
        self.assertEqual(result, {"hits": {"hits": []}})
        self.assertEqual(
            provider.body["query"]["bool"]["filter"],
            [{"term": {"tenant_id": "tenant-a"}}],
        )


if __name__ == "__main__":
    unittest.main()
