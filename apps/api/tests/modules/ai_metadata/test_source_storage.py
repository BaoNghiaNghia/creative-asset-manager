import unittest
from contextlib import asynccontextmanager

from app.domain.providers.contracts import AssetDownloadStream, OpenStoredAssetInput
from app.modules.ai_metadata.source_storage import PipelineSourceAssetStorage
from app.modules.pipeline.model import AssetPipelineModel


class SourceStorageTest(unittest.IsolatedAsyncioTestCase):
    async def test_source_stream_is_tenant_and_asset_scoped_and_closed(self):
        closed = False

        class Resolver:
            @asynccontextmanager
            async def open(self, *, tenant_id, pipeline):
                nonlocal closed
                self.tenant_id = tenant_id
                self.pipeline = pipeline

                async def body():
                    yield b"source-bytes"

                async def close():
                    nonlocal closed
                    closed = True

                stream = AssetDownloadStream(
                    body=body(), close=close, content_type="image/avif"
                )
                try:
                    yield stream
                finally:
                    await stream.close()

        resolver = Resolver()
        pipeline = AssetPipelineModel(
            id="pipeline-a",
            tenant_id="tenant-a",
            correlation_id="source:a",
            origin_type="source_asset",
            origin_id="source-a",
            source_asset_id="source-a",
            asset_id="asset-a",
        )
        provider = PipelineSourceAssetStorage(
            resolver, tenant_id="tenant-a", pipeline=pipeline
        )
        stream = await provider.open_asset(
            OpenStoredAssetInput(
                tenant_id="tenant-a",
                asset_id="asset-a",
                remote_file_id="source-asset",
            )
        )
        content = b"".join([chunk async for chunk in stream.body])
        await stream.close()

        self.assertEqual(content, b"source-bytes")
        self.assertEqual(resolver.tenant_id, "tenant-a")
        self.assertIs(resolver.pipeline, pipeline)
        self.assertTrue(closed)

    async def test_wrong_asset_is_rejected(self):
        pipeline = AssetPipelineModel(
            tenant_id="tenant-a",
            correlation_id="source:a",
            origin_type="source_asset",
            origin_id="source-a",
            source_asset_id="source-a",
            asset_id="asset-a",
        )
        provider = PipelineSourceAssetStorage(
            object(), tenant_id="tenant-a", pipeline=pipeline
        )
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            await provider.open_asset(
                OpenStoredAssetInput(
                    tenant_id="tenant-a",
                    asset_id="asset-b",
                    remote_file_id="source-asset",
                )
            )


if __name__ == "__main__":
    unittest.main()
