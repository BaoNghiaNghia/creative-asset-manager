import asyncio
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.domain.providers.contracts import AssetDownloadStream, ExternalAssetCandidate
from app.modules.assets.content_resolver import SourceAssetContentResolver, SourceAssetContentUnavailable
from app.modules.assets.model import SourceAssetModel
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.pipeline.content_resolver import (
    SourceAssetPipelineContentResolver,
)
from app.modules.pipeline.repository import AssetPipelineRepository
from app.modules.pipeline.stages import InvalidPipelineContent


class FakeProvider:
    def __init__(self):
        self.entered = False
        self.exited = False
        self.stream_closed = False
        self.input = None
        self.get_input = None

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.exited = True

    async def open_download_stream(self, input):
        self.input = input

        async def body():
            yield b"content"

        async def close():
            self.stream_closed = True

        return AssetDownloadStream(
            body=body(),
            close=close,
            content_type="image/png",
        )

    async def get_asset(self, input):
        self.get_input = input
        return ExternalAssetCandidate(
            source_type="google_drive",
            source_id=input.source_id,
            external_asset_id=input.external_asset_id,
            filename="moved-asset.png",
            mime_type="image/png",
            size_bytes=7,
            source_metadata={"parent_id": "new-folder-id"},
        )


class SourceAssetPipelineContentResolverTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )

    def tearDown(self):
        self.engine.dispose()

    def pipeline(self, *, tenant_id="tenant-a", metadata=None):
        with self.sessions() as session:
            assets = AssetRegistryRepository(session)
            source = assets.upsert_external_source(
                tenant_id=tenant_id,
                source_key="google-drive:connection-a",
                source_type="google_drive",
                source_metadata=metadata
                if metadata is not None
                else {"oauth_connection_id": "connection-a"},
            )
            source_asset = assets.upsert_source_asset(
                tenant_id=tenant_id,
                external_source_id=source.id,
                external_asset_id="drive-file-a",
                filename="asset.png",
                mime_type="image/png",
            )
            pipeline = AssetPipelineRepository(session).get_or_create(
                tenant_id=tenant_id,
                origin_type="source_asset",
                origin_id=source_asset.id,
                source_asset_id=source_asset.id,
            )
            session.commit()
            return pipeline

    def test_google_stream_and_provider_are_closed(self):
        pipeline = self.pipeline()
        provider = FakeProvider()
        token_calls = []

        async def resolve_token(connection_id):
            token_calls.append(connection_id)
            return "access-token"

        def provider_factory(provider_name, access_token):
            self.assertEqual(provider_name, "google-drive")
            self.assertEqual(access_token, "access-token")
            return provider

        resolver = SourceAssetPipelineContentResolver(
            self.sessions,
            token_resolver=resolve_token,
            source_provider_factory=provider_factory,
        )

        async def consume():
            async with resolver.open(
                tenant_id="tenant-a",
                pipeline=pipeline,
            ) as stream:
                self.assertEqual(
                    b"".join([chunk async for chunk in stream.body]),
                    b"content",
                )

        asyncio.run(consume())
        self.assertEqual(token_calls, ["connection-a"])
        self.assertEqual(provider.input.external_asset_id, "drive-file-a")
        self.assertTrue(provider.entered)
        self.assertTrue(provider.stream_closed)
        self.assertTrue(provider.exited)

    def test_resolver_enforces_tenant_ownership(self):
        pipeline = self.pipeline()
        resolver = SourceAssetPipelineContentResolver(
            self.sessions,
            token_resolver=lambda _connection_id: None,
        )

        async def open_wrong_tenant():
            async with resolver.open(
                tenant_id="tenant-b",
                pipeline=pipeline,
            ):
                pass

        with self.assertRaisesRegex(
            InvalidPipelineContent, "source asset is unavailable"
        ):
            asyncio.run(open_wrong_tenant())

    def test_missing_oauth_connection_fails_safely(self):
        pipeline = self.pipeline(metadata={})
        called = False

        async def resolve_token(_connection_id):
            nonlocal called
            called = True
            return "unexpected"

        resolver = SourceAssetPipelineContentResolver(
            self.sessions,
            token_resolver=resolve_token,
        )

        async def open_missing_connection():
            async with resolver.open(
                tenant_id="tenant-a",
                pipeline=pipeline,
            ):
                pass

        with self.assertRaisesRegex(
            InvalidPipelineContent,
            "source OAuth connection is unavailable",
        ):
            asyncio.run(open_missing_connection())
        self.assertFalse(called)


    def test_generic_resolver_passes_range_header(self):
        pipeline = self.pipeline()
        provider = FakeProvider()

        async def token(_connection_id):
            return "token"

        resolver = SourceAssetContentResolver(
            self.sessions, token_resolver=token,
            source_provider_factory=lambda _name, _token: provider,
        )

        async def consume():
            async with resolver.open(
                tenant_id="tenant-a", source_asset_id=pipeline.source_asset_id,
                range_header="bytes=0-99",
            ) as stream:
                self.assertEqual(b"".join([chunk async for chunk in stream.body]), b"content")

        asyncio.run(consume())
        self.assertEqual(provider.input.range_header, "bytes=0-99")
        self.assertTrue(provider.stream_closed)

    def test_generic_resolver_refreshes_moved_deleted_source_asset(self):
        pipeline = self.pipeline()
        with self.sessions() as session:
            from datetime import datetime, timezone
            asset = session.get(SourceAssetModel, pipeline.source_asset_id)
            asset.deleted_at = datetime.now(timezone.utc)
            session.commit()
        provider = FakeProvider()

        async def token(_connection_id):
            return "token"

        resolver = SourceAssetContentResolver(
            self.sessions,
            token_resolver=token,
            source_provider_factory=lambda _name, _token: provider,
        )

        async def open_moved():
            async with resolver.open(
                tenant_id="tenant-a",
                source_asset_id=pipeline.source_asset_id,
            ) as stream:
                self.assertEqual(
                    b"".join([chunk async for chunk in stream.body]),
                    b"content",
                )

        asyncio.run(open_moved())
        self.assertEqual(provider.get_input.external_asset_id, "drive-file-a")
        with self.sessions() as session:
            asset = session.get(SourceAssetModel, pipeline.source_asset_id)
            self.assertIsNone(asset.deleted_at)
            self.assertEqual(asset.filename, "moved-asset.png")
            self.assertEqual(asset.source_metadata["parents"], ["new-folder-id"])

    def test_generic_resolver_rejects_deleted_source_missing_from_provider(self):
        pipeline = self.pipeline()
        with self.sessions() as session:
            from datetime import datetime, timezone
            asset = session.get(SourceAssetModel, pipeline.source_asset_id)
            asset.deleted_at = datetime.now(timezone.utc)
            session.commit()
        provider = FakeProvider()

        async def missing(_input):
            raise LookupError("not found")

        provider.get_asset = missing
        resolver = SourceAssetContentResolver(
            self.sessions,
            token_resolver=lambda _connection_id: asyncio.sleep(0, result="token"),
            source_provider_factory=lambda _name, _token: provider,
        )

        async def open_deleted():
            async with resolver.open(
                tenant_id="tenant-a",
                source_asset_id=pipeline.source_asset_id,
            ):
                pass

        with self.assertRaisesRegex(
            SourceAssetContentUnavailable,
            "source asset is unavailable",
        ):
            asyncio.run(open_deleted())


if __name__ == "__main__":
    unittest.main()
