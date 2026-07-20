import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.database import Base
from app.core.redaction import redact_url_queries
from app.modules.assets.model import AssetModel
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.auth_persistence.encryption import TokenCipher
from app.modules.external_ingestion.model import AssetIngestionItemModel, ExternalApiCredentialModel
from app.modules.external_ingestion.repository import ExternalIngestionRepository
from app.modules.external_ingestion.schema import AssetIngestionRequest
from app.modules.external_ingestion.service import ExternalIngestionService
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.retention.service import CleanupAlreadyRunning, RetentionCleanupService


class SensitiveUrlRetentionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{self.tmp.name}/test.db")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.cipher = TokenCipher({"v1": b"x" * 32}, "v1")
        with self.sessions() as session:
            source = AssetRegistryRepository(session).upsert_external_source(
                tenant_id="tenant-a", source_key="supplier", source_type="external_api"
            )
            credential = ExternalIngestionRepository(session).create_credential(
                tenant_id="tenant-a", external_source_id=source.id,
                name="supplier", raw_key="x" * 32,
            )
            AssetRegistryRepository(session).create_asset(
                tenant_id="tenant-a", content_hash="a" * 64
            )
            source_b = AssetRegistryRepository(session).upsert_external_source(
                tenant_id="tenant-b", source_key="supplier", source_type="external_api"
            )
            credential_b = ExternalIngestionRepository(session).create_credential(
                tenant_id="tenant-b", external_source_id=source_b.id,
                name="supplier", raw_key="y" * 32,
            )
            session.commit()
            self.sources = {"tenant-a": source.id, "tenant-b": source_b.id}
            self.credentials = {"tenant-a": credential.id, "tenant-b": credential_b.id}
            self.source_id = source.id
            self.credential_id = credential.id

    def tearDown(self):
        self.engine.dispose(); self.tmp.cleanup()

    def ingest(self, suffix="1", tenant_id="tenant-a"):
        with self.sessions() as session:
            repository = ExternalIngestionRepository(
                session, url_cipher=self.cipher, url_retention_hours=24
            )
            credential = session.get(ExternalApiCredentialModel, self.credentials[tenant_id])
            request = AssetIngestionRequest.model_validate({
                "source_id": self.sources[tenant_id],
                "items": [{
                    "external_asset_id": f"asset-{suffix}",
                    "download_url": f"https://cdn.example.test/a.jpg?signature=secret-{suffix}",
                    "filename": "a.jpg",
                }],
            })
            return ExternalIngestionService(
                repository, ProcessingRepository(session)
            ).create(
                credential=credential, idempotency_key=f"key-{suffix}", request=request
            )

    def test_url_is_encrypted_and_job_payload_contains_stable_ids_only(self):
        ingestion = self.ingest()
        with self.sessions() as session:
            item = session.scalar(select(AssetIngestionItemModel))
            job = session.scalar(select(ProcessingJobModel))
            self.assertIsNone(item.download_url)
            self.assertNotIn("secret-1", item.download_url_ciphertext)
            self.assertEqual(
                ExternalIngestionRepository(session, url_cipher=self.cipher).resolve_download_url(
                    tenant_id="tenant-a", item_id=item.id
                ),
                "https://cdn.example.test/a.jpg?signature=secret-1",
            )
            self.assertEqual(set(job.payload_json), {"ingestion_id", "ingestion_item_id"})
            persisted = session.get(type(ingestion), ingestion.id)
            self.assertNotIn("download_url", str(persisted.request_json))

    def test_error_redaction_removes_query_parameters(self):
        value = redact_url_queries(
            "download failed https://cdn.example.test/a.jpg?signature=top-secret"
        )
        self.assertEqual(value, "download failed https://cdn.example.test/a.jpg")

    def test_cleanup_dry_run_then_redacts_url_and_preserves_asset_identity(self):
        self.ingest()
        old = datetime.now(timezone.utc) - timedelta(days=3)
        with self.sessions() as session:
            item = session.scalar(select(AssetIngestionItemModel))
            item.download_url_expires_at = old
            session.commit()
        settings = Settings(
            RETENTION_CLEANUP_BATCH_SIZE=10, RETENTION_CLEANUP_MAX_ROWS=10
        )
        service = RetentionCleanupService(self.sessions, settings)
        dry = service.create_run(
            tenant_id="tenant-a", record_types=("ingestion_urls",),
            dry_run=True, policy_name="dry", now=datetime.now(timezone.utc),
        )
        service.execute(tenant_id="tenant-a", run_id=dry.id)
        with self.sessions() as session:
            item = session.scalar(select(AssetIngestionItemModel))
            self.assertIsNotNone(item.download_url_ciphertext)
        run = service.create_run(
            tenant_id="tenant-a", record_types=("ingestion_urls",),
            policy_name="real", now=datetime.now(timezone.utc),
        )
        service.execute(tenant_id="tenant-a", run_id=run.id)
        with self.sessions() as session:
            item = session.scalar(select(AssetIngestionItemModel))
            self.assertIsNone(item.download_url_ciphertext)
            self.assertIsNotNone(item.download_url_redacted_at)
            self.assertEqual(session.scalar(select(func.count()).select_from(AssetModel)), 1)

    def test_cleanup_is_tenant_isolated(self):
        self.ingest("a")
        self.ingest("b", tenant_id="tenant-b")
        old = datetime.now(timezone.utc) - timedelta(days=3)
        with self.sessions() as session:
            for item in session.scalars(select(AssetIngestionItemModel)):
                item.download_url_expires_at = old
            session.commit()
        settings = Settings(RETENTION_CLEANUP_BATCH_SIZE=10, RETENTION_CLEANUP_MAX_ROWS=10)
        service = RetentionCleanupService(self.sessions, settings)
        run = service.create_run(
            tenant_id="tenant-a", record_types=("ingestion_urls",),
            now=datetime.now(timezone.utc),
        )
        service.execute(tenant_id="tenant-a", run_id=run.id)
        with self.sessions() as session:
            tenant_a = session.scalar(select(AssetIngestionItemModel).where(
                AssetIngestionItemModel.tenant_id == "tenant-a"
            ))
            tenant_b = session.scalar(select(AssetIngestionItemModel).where(
                AssetIngestionItemModel.tenant_id == "tenant-b"
            ))
            self.assertIsNone(tenant_a.download_url_ciphertext)
            self.assertIsNotNone(tenant_b.download_url_ciphertext)

    def test_cleanup_cancellation_preserves_queued_records(self):
        self.ingest("cancel")
        settings = Settings(RETENTION_CLEANUP_BATCH_SIZE=10, RETENTION_CLEANUP_MAX_ROWS=10)
        service = RetentionCleanupService(self.sessions, settings)
        run = service.create_run(
            tenant_id="tenant-a", record_types=("ingestion_urls",),
            age_seconds=0, policy_name="cancel",
        )
        service.cancel(tenant_id="tenant-a", run_id=run.id)
        result = service.execute(tenant_id="tenant-a", run_id=run.id)
        self.assertEqual(result.status, "cancelled")
        with self.sessions() as session:
            item = session.scalar(select(AssetIngestionItemModel).where(
                AssetIngestionItemModel.tenant_id == "tenant-a"
            ))
            self.assertIsNotNone(item.download_url_ciphertext)

    def test_cleanup_is_resumable_bounded_and_scope_locked(self):
        self.ingest("1"); self.ingest("2")
        old = datetime.now(timezone.utc) - timedelta(days=3)
        with self.sessions() as session:
            for item in session.scalars(select(AssetIngestionItemModel)):
                item.download_url_expires_at = old
            session.commit()
        settings = Settings(
            RETENTION_CLEANUP_BATCH_SIZE=1, RETENTION_CLEANUP_MAX_ROWS=1
        )
        service = RetentionCleanupService(self.sessions, settings)
        run = service.create_run(
            tenant_id="tenant-a", record_types=("ingestion_urls",),
            max_rows=3, now=datetime.now(timezone.utc),
        )
        with self.assertRaises(CleanupAlreadyRunning):
            service.create_run(
                tenant_id="tenant-a", record_types=("ingestion_urls",),
                now=datetime.now(timezone.utc),
            )
        first = service.execute(tenant_id="tenant-a", run_id=run.id)
        self.assertEqual(first.status, "running")
        self.assertEqual(first.checkpoint_version, 1)
        second = service.execute(tenant_id="tenant-a", run_id=run.id)
        self.assertGreaterEqual(second.checkpoint_version, 2)
        with self.sessions() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(AssetIngestionItemModel).where(
                    AssetIngestionItemModel.download_url_ciphertext.is_not(None)
                )), 0
            )
