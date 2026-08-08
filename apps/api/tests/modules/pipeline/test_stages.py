import asyncio
import io
import unittest
from contextlib import asynccontextmanager

from PIL import Image, features
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.domain.providers.contracts import AssetDownloadStream
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.pipeline.repository import AssetPipelineRepository
from app.modules.pipeline.stages import ProviderDownloadStage, SourceContentTooLarge


async def _close():
    return None


class BytesResolver:
    def __init__(self, content: bytes, content_type: str = "image/png"):
        self.content = content
        self.content_type = content_type

    @asynccontextmanager
    async def open(self, *, tenant_id, pipeline):
        async def body():
            midpoint = len(self.content) // 2
            yield self.content[:midpoint]
            yield self.content[midpoint:]

        yield AssetDownloadStream(body=body(), close=_close, content_type=self.content_type)


def png(color):
    output = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(output, format="PNG")
    return output.getvalue()


def webp(color):
    output = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(output, format="WEBP")
    return output.getvalue()


def avif(color):
    if not features.check("avif"):
        raise unittest.SkipTest("Pillow AVIF decoder is unavailable")
    output = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(output, format="AVIF")
    return output.getvalue()


class ProviderDownloadStageTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose()

    def pipeline(self, source_type, source_key, external_id, filename):
        with self.sessions() as session:
            assets = AssetRegistryRepository(session)
            source = assets.upsert_external_source(
                tenant_id="tenant-a", source_key=source_key, source_type=source_type,
            )
            source_asset = assets.upsert_source_asset(
                tenant_id="tenant-a", external_source_id=source.id,
                external_asset_id=external_id, filename=filename, mime_type="image/png",
            )
            pipeline = AssetPipelineRepository(session).get_or_create(
                tenant_id="tenant-a", origin_type="source_asset",
                origin_id=source_asset.id, source_asset_id=source_asset.id,
            )
            session.commit()
            return pipeline

    def test_same_bytes_from_drive_and_sharepoint_reuse_asset(self):
        content = png("red")
        first = self.pipeline("google_drive", "drive", "one", "one.png")
        second = self.pipeline("sharepoint", "sharepoint", "two", "renamed.png")
        stage = ProviderDownloadStage(self.sessions, BytesResolver(content))
        one = asyncio.run(stage.execute(tenant_id="tenant-a", pipeline=first))
        two = asyncio.run(stage.execute(tenant_id="tenant-a", pipeline=second))
        self.assertEqual(one.asset_id, two.asset_id)
        self.assertFalse(one.duplicate)
        self.assertTrue(two.duplicate)

    def test_same_filename_with_different_bytes_creates_assets(self):
        first = self.pipeline("google_drive", "drive", "one", "same.png")
        second = self.pipeline("sharepoint", "sharepoint", "two", "same.png")
        one = asyncio.run(ProviderDownloadStage(
            self.sessions, BytesResolver(png("red"))
        ).execute(tenant_id="tenant-a", pipeline=first))
        two = asyncio.run(ProviderDownloadStage(
            self.sessions, BytesResolver(png("blue"))
        ).execute(tenant_id="tenant-a", pipeline=second))
        self.assertNotEqual(one.asset_id, two.asset_id)
        self.assertNotEqual(one.content_hash, two.content_hash)

    def test_webp_is_accepted(self):
        pipeline = self.pipeline("google_drive", "drive", "webp", "one.webp")
        result = asyncio.run(ProviderDownloadStage(
            self.sessions, BytesResolver(webp("green"), "image/webp")
        ).execute(tenant_id="tenant-a", pipeline=pipeline))
        self.assertIsNotNone(result.asset_id)

    def test_avif_is_accepted(self):
        pipeline = self.pipeline("google_drive", "drive", "avif", "one.avif")
        result = asyncio.run(ProviderDownloadStage(
            self.sessions, BytesResolver(avif("green"), "image/avif")
        ).execute(tenant_id="tenant-a", pipeline=pipeline))
        self.assertIsNotNone(result.asset_id)

    def test_byte_limit_raises_stable_oversized_error(self):
        pipeline = self.pipeline("google_drive", "drive", "large", "large.png")
        stage = ProviderDownloadStage(
            self.sessions,
            BytesResolver(png("red")),
            max_bytes=1,
        )
        with self.assertRaises(SourceContentTooLarge):
            asyncio.run(stage.execute(tenant_id="tenant-a", pipeline=pipeline))
