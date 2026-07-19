import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.domain.providers.contracts import (
    StorageProviderError,
    StoreAssetInput,
    StoredAsset,
)
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.storage.repository import ManagedStorageRepository
from app.modules.storage.service import ManagedAssetStorageService


async def body(value: bytes):
    yield value


class FakeStorageProvider:
    provider_name = "google_drive_managed"

    def __init__(self, *, failure: StorageProviderError | None = None):
        self.failure = failure
        self.calls = 0

    async def store_asset(self, input):
        self.calls += 1
        if self.failure:
            raise self.failure
        return StoredAsset(
            storage_key="google-drive:remote-1",
            content_hash=input.content_hash,
            storage_provider=self.provider_name,
            remote_file_id="remote-1",
            remote_folder_id="managed-root",
            web_url="https://drive.google.com/file/remote-1",
        )

    async def store_metadata_sidecar(self, input):
        raise NotImplementedError


class ManagedAssetStorageServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.assets = AssetRegistryRepository(self.session)
        self.asset = self.assets.create_asset(
            tenant_id="tenant-a", content_hash="d" * 64, mime_type="image/png"
        )
        self.session.commit()
        self.storage = ManagedStorageRepository(self.session)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def input(self):
        return StoreAssetInput(
            tenant_id="tenant-a",
            asset_id=self.asset.id,
            content_hash=self.asset.content_hash,
            body=body(b"content"),
            filename="asset.png",
        )

    async def test_persists_remote_identity_and_is_idempotent(self) -> None:
        provider = FakeStorageProvider()
        service = ManagedAssetStorageService(
            self.assets, self.storage, enabled=True
        )
        first = await service.store(self.input(), provider)
        second = await service.store(self.input(), provider)
        record = self.storage.get("tenant-a", self.asset.id, provider.provider_name)
        self.assertEqual(first.remote_file_id, second.remote_file_id)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(record.status, "stored")
        self.assertEqual(record.remote_file_id, "remote-1")
        self.assertEqual(record.remote_folder_id, "managed-root")
        self.assertEqual(record.web_url, first.web_url)

    async def test_retryable_failure_updates_same_storage_record(self) -> None:
        provider = FakeStorageProvider(
            failure=StorageProviderError("temporary", retryable=True)
        )
        service = ManagedAssetStorageService(
            self.assets, self.storage, enabled=True, max_attempts=3
        )
        with self.assertRaises(StorageProviderError):
            await service.store(self.input(), provider)
        record = self.storage.get("tenant-a", self.asset.id, provider.provider_name)
        self.assertEqual(record.status, "retry")
        self.assertEqual(record.attempt_count, 1)
        self.assertIsNotNone(record.next_attempt_at)
        provider.failure = None
        result = await service.store(self.input(), provider)
        self.assertEqual(result.remote_file_id, "remote-1")
        self.assertEqual(record.status, "stored")
        self.assertEqual(record.attempt_count, 2)

    async def test_non_retryable_failure_becomes_terminal(self) -> None:
        provider = FakeStorageProvider(
            failure=StorageProviderError("forbidden", retryable=False)
        )
        service = ManagedAssetStorageService(self.assets, self.storage, enabled=True)
        with self.assertRaises(StorageProviderError):
            await service.store(self.input(), provider)
        record = self.storage.get("tenant-a", self.asset.id, provider.provider_name)
        self.assertEqual(record.status, "failed")

    async def test_disabled_service_does_not_call_provider(self) -> None:
        provider = FakeStorageProvider()
        service = ManagedAssetStorageService(self.assets, self.storage, enabled=False)
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            await service.store(self.input(), provider)
        self.assertEqual(provider.calls, 0)
