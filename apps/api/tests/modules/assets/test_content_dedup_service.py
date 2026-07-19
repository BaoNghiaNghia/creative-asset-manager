import asyncio
import hashlib
import tempfile
import threading
import unittest
from pathlib import Path

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.assets.content_dedup_service import (
    ContentDeduplicationDisabledError,
    ContentDeduplicationService,
)
from app.modules.assets.model import AssetModel, AssetSourceLinkModel
from app.modules.assets.repository import AssetRegistryRepository


async def chunks(*values: bytes):
    for value in values:
        await asyncio.sleep(0)
        yield value


class ContentDeduplicationServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.repository = AssetRegistryRepository(self.session)
        self.source_a = self.repository.upsert_external_source(
            tenant_id="tenant-a", source_key="google", source_type="google_drive"
        )
        self.source_b = self.repository.upsert_external_source(
            tenant_id="tenant-a", source_key="sharepoint", source_type="sharepoint"
        )
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _source_asset(self, source_id: str, external_id: str, filename: str):
        source_asset = self.repository.upsert_source_asset(
            tenant_id="tenant-a",
            external_source_id=source_id,
            external_asset_id=external_id,
            filename=filename,
            mime_type="image/png",
        )
        self.session.commit()
        return source_asset

    async def test_same_bytes_from_different_sources_reuse_asset(self) -> None:
        google = self._source_asset(self.source_a.id, "google-1", "first.png")
        sharepoint = self._source_asset(self.source_b.id, "sp-1", "second.png")
        service = ContentDeduplicationService(self.repository, enabled=True)

        first = await service.ingest(
            tenant_id="tenant-a", source_asset_id=google.id,
            content_stream=chunks(b"same", b"-bytes"),
        )
        second = await service.ingest(
            tenant_id="tenant-a", source_asset_id=sharepoint.id,
            content_stream=chunks(b"same-bytes"),
        )

        self.assertEqual(first.asset.id, second.asset.id)
        self.assertTrue(second.reused_asset)
        asset_count = self.session.scalar(select(func.count()).select_from(AssetModel))
        self.assertEqual(asset_count, 1)

    async def test_same_filename_with_different_bytes_creates_assets(self) -> None:
        first_source = self._source_asset(self.source_a.id, "google-1", "same.png")
        second_source = self._source_asset(self.source_a.id, "google-2", "same.png")
        service = ContentDeduplicationService(self.repository, enabled=True)

        first = await service.ingest(
            tenant_id="tenant-a", source_asset_id=first_source.id,
            content_stream=chunks(b"first"),
        )
        second = await service.ingest(
            tenant_id="tenant-a", source_asset_id=second_source.id,
            content_stream=chunks(b"second"),
        )

        self.assertNotEqual(first.asset.id, second.asset.id)

    async def test_reingest_does_not_duplicate_source_link(self) -> None:
        source_asset = self._source_asset(self.source_a.id, "google-1", "asset.png")
        service = ContentDeduplicationService(self.repository, enabled=True)

        await service.ingest(
            tenant_id="tenant-a", source_asset_id=source_asset.id,
            content_stream=chunks(b"same"),
        )
        await service.ingest(
            tenant_id="tenant-a", source_asset_id=source_asset.id,
            content_stream=chunks(b"same"),
        )

        link_count = self.session.scalar(
            select(func.count()).select_from(AssetSourceLinkModel)
        )
        self.assertEqual(link_count, 1)

    async def test_provider_version_can_skip_download_after_hash_exists(self) -> None:
        source_asset = self.repository.upsert_source_asset(
            tenant_id="tenant-a",
            external_source_id=self.source_a.id,
            external_asset_id="google-1",
            provider_version="v1",
        )
        self.session.commit()
        service = ContentDeduplicationService(self.repository, enabled=True)
        await service.ingest(
            tenant_id="tenant-a", source_asset_id=source_asset.id,
            content_stream=chunks(b"original"), provider_version="v1",
        )

        reused = await service.ingest(
            tenant_id="tenant-a", source_asset_id=source_asset.id,
            content_stream=None, provider_version="v1",
        )

        self.assertTrue(reused.reused_provider_version)
        self.assertEqual(reused.bytes_hashed, 0)

    async def test_changed_provider_version_requires_new_content_hash(self) -> None:
        source_asset = self.repository.upsert_source_asset(
            tenant_id="tenant-a",
            external_source_id=self.source_a.id,
            external_asset_id="google-1",
            provider_version="v1",
        )
        self.session.commit()
        service = ContentDeduplicationService(self.repository, enabled=True)
        original = await service.ingest(
            tenant_id="tenant-a", source_asset_id=source_asset.id,
            content_stream=chunks(b"original"), provider_version="v1",
        )

        self.repository.upsert_source_asset(
            tenant_id="tenant-a",
            external_source_id=self.source_a.id,
            external_asset_id="google-1",
            provider_version="v2",
        )
        self.session.commit()
        changed = await service.ingest(
            tenant_id="tenant-a", source_asset_id=source_asset.id,
            content_stream=chunks(b"changed"), provider_version="v2",
        )

        self.assertFalse(changed.reused_provider_version)
        self.assertNotEqual(original.asset.id, changed.asset.id)
        link_count = self.session.scalar(
            select(func.count()).select_from(AssetSourceLinkModel)
        )
        self.assertEqual(link_count, 1)

    async def test_feature_flag_must_be_enabled(self) -> None:
        source_asset = self._source_asset(self.source_a.id, "google-1", "asset.png")
        service = ContentDeduplicationService(self.repository, enabled=False)

        with self.assertRaises(ContentDeduplicationDisabledError):
            await service.ingest(
                tenant_id="tenant-a", source_asset_id=source_asset.id,
                content_stream=chunks(b"content"),
            )


class BarrierRepository(AssetRegistryRepository):
    def __init__(self, session: Session, barrier: threading.Barrier):
        super().__init__(session)
        self.barrier = barrier
        self.waited = False

    def find_asset_by_content_hash(self, tenant_id: str, content_hash: str):
        asset = super().find_asset_by_content_hash(tenant_id, content_hash)
        if asset is None and not self.waited:
            self.waited = True
            self.session.rollback()
            self.barrier.wait(timeout=10)
        return asset


class ConcurrentContentDeduplicationTest(unittest.TestCase):
    def test_two_transactions_converge_on_database_unique_constraint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "concurrency.db"
            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False, "timeout": 20},
            )

            @event.listens_for(engine, "connect")
            def configure_sqlite(dbapi_connection, _connection_record):
                dbapi_connection.execute("PRAGMA journal_mode=WAL")
                dbapi_connection.execute("PRAGMA foreign_keys=ON")
                dbapi_connection.execute("PRAGMA busy_timeout=20000")

            Base.metadata.create_all(engine)
            with Session(engine, expire_on_commit=False) as setup_session:
                setup = AssetRegistryRepository(setup_session)
                source = setup.upsert_external_source(
                    tenant_id="tenant-a", source_key="drive", source_type="google_drive"
                )
                first = setup.upsert_source_asset(
                    tenant_id="tenant-a", external_source_id=source.id,
                    external_asset_id="first",
                )
                second = setup.upsert_source_asset(
                    tenant_id="tenant-a", external_source_id=source.id,
                    external_asset_id="second",
                )
                setup_session.commit()
                source_asset_ids = (first.id, second.id)

            barrier = threading.Barrier(2)
            results: list[str] = []
            errors: list[BaseException] = []

            def ingest(source_asset_id: str) -> None:
                try:
                    with Session(engine, expire_on_commit=False) as session:
                        repository = BarrierRepository(session, barrier)
                        service = ContentDeduplicationService(repository, enabled=True)
                        result = asyncio.run(service.ingest(
                            tenant_id="tenant-a",
                            source_asset_id=source_asset_id,
                            content_stream=chunks(b"concurrent-content"),
                        ))
                        results.append(result.asset.id)
                except BaseException as exc:
                    errors.append(exc)

            threads = [
                threading.Thread(target=ingest, args=(source_asset_id,))
                for source_asset_id in source_asset_ids
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

            self.assertEqual(errors, [])
            self.assertEqual(len(set(results)), 1)
            with Session(engine) as verification:
                asset_count = verification.scalar(
                    select(func.count()).select_from(AssetModel)
                )
                link_count = verification.scalar(
                    select(func.count()).select_from(AssetSourceLinkModel)
                )
                stored_hash = verification.scalar(select(AssetModel.content_hash))
            self.assertEqual(asset_count, 1)
            self.assertEqual(link_count, 2)
            self.assertEqual(
                stored_hash,
                hashlib.sha256(b"concurrent-content").hexdigest(),
            )
            engine.dispose()
