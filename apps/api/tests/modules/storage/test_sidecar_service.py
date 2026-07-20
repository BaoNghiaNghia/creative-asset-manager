import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.domain.providers.contracts import StorageProviderError, StoredMetadataSidecar
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.storage.model import MetadataSidecarExportModel
from app.modules.storage.sidecar_document import MetadataSidecarDocumentBuilder
from app.modules.storage.sidecar_repository import MetadataSidecarRepository
from app.modules.storage.sidecar_service import MetadataSidecarExportService


class FakeSidecarProvider:
    provider_name = "google_drive_managed"

    def __init__(self):
        self.calls = 0
        self.inputs = []
        self.failure = None

    async def store_asset(self, input):
        raise NotImplementedError

    async def store_metadata_sidecar(self, input):
        self.calls += 1
        self.inputs.append(input)
        if self.failure:
            raise self.failure
        return StoredMetadataSidecar(
            storage_key="google-drive:sidecar-1",
            remote_file_id="sidecar-1",
            remote_folder_id="managed-root",
            web_url="https://drive.google.com/file/sidecar-1",
            document_hash=input.document_hash,
        )


class MetadataSidecarExportServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        assets = AssetRegistryRepository(self.session)
        source = assets.upsert_external_source(
            tenant_id="tenant-a",
            source_key="drive-a",
            source_type="google_drive",
        )
        source_asset = assets.upsert_source_asset(
            tenant_id="tenant-a",
            external_source_id=source.id,
            external_asset_id="drive-file-1",
            filename="cat.png",
            provider_checksum="checksum-1",
            source_metadata={
                "signed_url": "https://example.test/file?signature=secret",
                "access_token": "source-secret",
            },
        )
        self.asset = assets.create_asset(
            tenant_id="tenant-a",
            content_hash="a" * 64,
            mime_type="image/png",
        )
        assets.link_source_asset(
            tenant_id="tenant-a",
            asset_id=self.asset.id,
            source_asset_id=source_asset.id,
        )
        metadata = AiMetadataRepository(self.session)
        profile = metadata.create_profile(
            tenant_id="tenant-a",
            profile_name="default",
            profile_version="1",
            prompt_template="Analyze",
        )
        self.analysis = metadata.create_analysis(
            tenant_id="tenant-a",
            asset_id=self.asset.id,
            metadata_profile_id=profile.id,
            prompt_version="prompt-1",
            pipeline_version="pipeline-1",
            ai_provider="provider-a",
            ai_model="model-a",
        )
        metadata.mark_running(self.analysis.id)
        metadata.complete_analysis(
            analysis_id=self.analysis.id,
            metadata={
                "subject": "cat",
                "api_token": "metadata-secret",
                "preview": "https://cdn.test/cat.png?x-goog-signature=secret",
                "nested": {"password": "hidden", "safe": "mama"},
            },
            raw_response={"authorization": "raw-secret"},
            store_raw_response=True,
            search_projection={"search_text": "cat"},
            search_projection_version="projection-v1",
        )
        self.session.commit()
        self.repository = MetadataSidecarRepository(self.session)
        self.builder = MetadataSidecarDocumentBuilder(self.session)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def service(self, enabled=True):
        return MetadataSidecarExportService(
            self.repository,
            self.builder,
            enabled=enabled,
            max_attempts=3,
        )

    async def test_builds_safe_postgresql_document_and_is_idempotent(self) -> None:
        provider = FakeSidecarProvider()
        first = await self.service().export(
            tenant_id="tenant-a",
            analysis_id=self.analysis.id,
            provider=provider,
        )
        second = await self.service().export(
            tenant_id="tenant-a",
            analysis_id=self.analysis.id,
            provider=provider,
        )
        document = provider.inputs[0].metadata
        self.assertEqual(first.remote_file_id, second.remote_file_id)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(document["authoritative_source"], "postgresql")
        self.assertEqual(document["asset"]["content_hash"], "a" * 64)
        self.assertEqual(document["source_references"][0]["external_asset_id"], "drive-file-1")
        self.assertEqual(document["analysis"]["analysis_id"], self.analysis.id)
        self.assertEqual(document["search_projection_version"], "projection-v1")
        self.assertNotIn("api_token", document["metadata_json"])
        self.assertNotIn("password", document["metadata_json"]["nested"])
        self.assertEqual(document["metadata_json"]["preview"], "[redacted-url]")
        serialized = __import__("json").dumps(document)
        self.assertNotIn("source-secret", serialized)
        self.assertNotIn("raw-secret", serialized)
        self.assertNotIn("metadata-secret", serialized)

    async def test_changed_projection_version_updates_same_export_record(self) -> None:
        provider = FakeSidecarProvider()
        await self.service().export(
            tenant_id="tenant-a", analysis_id=self.analysis.id, provider=provider
        )
        old_hash = provider.inputs[0].document_hash
        self.analysis.search_projection_version = "projection-v2"
        self.session.commit()
        await self.service().export(
            tenant_id="tenant-a", analysis_id=self.analysis.id, provider=provider
        )
        record = self.repository.get(self.analysis.id, provider.provider_name)
        self.assertEqual(provider.calls, 2)
        self.assertNotEqual(old_hash, provider.inputs[1].document_hash)
        self.assertEqual(
            self.session.query(MetadataSidecarExportModel).count(),
            1,
        )
        self.assertEqual(record.remote_file_id, "sidecar-1")

    async def test_failure_retries_without_rolling_back_completed_analysis(self) -> None:
        provider = FakeSidecarProvider()
        provider.failure = StorageProviderError("temporary", retryable=True)
        with self.assertRaises(StorageProviderError):
            await self.service().export(
                tenant_id="tenant-a",
                analysis_id=self.analysis.id,
                provider=provider,
            )
        record = self.repository.get(self.analysis.id, provider.provider_name)
        self.session.refresh(self.analysis)
        self.assertEqual(self.analysis.status, "completed")
        self.assertEqual(record.status, "retry")
        self.assertEqual(record.attempt_count, 1)
        provider.failure = None
        await self.service().export(
            tenant_id="tenant-a",
            analysis_id=self.analysis.id,
            provider=provider,
        )
        self.assertEqual(record.status, "stored")
        self.assertEqual(record.attempt_count, 2)

    async def test_feature_flag_and_tenant_boundary(self) -> None:
        provider = FakeSidecarProvider()
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            await self.service(enabled=False).export(
                tenant_id="tenant-a", analysis_id=self.analysis.id, provider=provider
            )
        with self.assertRaises(LookupError):
            await self.service().export(
                tenant_id="tenant-b", analysis_id=self.analysis.id, provider=provider
            )
        self.assertEqual(provider.calls, 0)


if __name__ == "__main__":
    unittest.main()
