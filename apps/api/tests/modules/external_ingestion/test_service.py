import tempfile
import threading
import unittest
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.external_ingestion.model import (
    AssetIngestionItemModel,
    AssetIngestionModel,
    ExternalApiCredentialModel,
)
from app.modules.external_ingestion.repository import (
    ExternalIngestionRepository,
    IdempotencyConflictError,
)
from app.modules.external_ingestion.schema import AssetIngestionRequest
from app.modules.external_ingestion.service import ExternalIngestionService, canonical_request
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository


TOKEN = "supplier-test-token-0000000000000001"


def request(source_id: str, filename: str = "cat.jpg") -> AssetIngestionRequest:
    return AssetIngestionRequest.model_validate(
        {
            "source_id": source_id,
            "items": [
                {
                    "external_asset_id": "supplier/cat-001",
                    "download_url": "https://cdn.example.com/cat.jpg?signature=secret",
                    "checksum": "sha256:abc",
                    "filename": filename,
                    "modified_at": "2026-07-19T08:30:00+07:00",
                },
                {
                    "external_asset_id": "supplier/cat-002",
                    "download_url": "https://cdn.example.com/cat-2.jpg",
                },
            ],
        }
    )


class ExternalIngestionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name) / "ingestion.db"
        self.engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.sessions() as session:
            source = AssetRegistryRepository(session).upsert_external_source(
                tenant_id="tenant-a",
                source_key="supplier-primary",
                source_type="external_api",
            )
            credential = ExternalIngestionRepository(session).create_credential(
                tenant_id="tenant-a",
                external_source_id=source.id,
                name="supplier",
                raw_key=TOKEN,
                rate_limit_per_minute=100,
            )
            session.commit()
            self.source_id = source.id
            self.credential_id = credential.id

    def tearDown(self) -> None:
        self.engine.dispose()
        self.directory.cleanup()

    def create(self, key: str, body: AssetIngestionRequest | None = None):
        with self.sessions() as session:
            repository = ExternalIngestionRepository(session)
            credential = session.get(ExternalApiCredentialModel, self.credential_id)
            return ExternalIngestionService(
                repository,
                ProcessingRepository(session),
            ).create(
                credential=credential,
                idempotency_key=key,
                request=body or request(self.source_id),
            )

    def test_canonical_hash_is_stable_and_normalizes_timestamp(self) -> None:
        first = request(self.source_id)
        second = AssetIngestionRequest.model_validate_json(first.model_dump_json())
        first_document, first_bytes, first_hash = canonical_request(first)
        second_document, second_bytes, second_hash = canonical_request(second)
        self.assertEqual(first_document, second_document)
        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(first_hash, second_hash)
        self.assertEqual(first_document["items"][0]["modified_at"], "2026-07-19T01:30:00Z")

    def test_same_key_same_body_reuses_ingestion_and_jobs(self) -> None:
        first = self.create("supplier-page-001")
        second = self.create("supplier-page-001")
        self.assertEqual(first.id, second.id)
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(AssetIngestionModel)), 1)
            self.assertEqual(session.scalar(select(func.count()).select_from(AssetIngestionItemModel)), 2)
            self.assertEqual(session.scalar(select(func.count()).select_from(ProcessingJobModel)), 2)

    def test_same_key_different_body_conflicts(self) -> None:
        self.create("supplier-page-001")
        with self.assertRaises(IdempotencyConflictError):
            self.create("supplier-page-001", request(self.source_id, "different.jpg"))

    def test_concurrent_same_request_converges_on_one_ingestion(self) -> None:
        barrier = threading.Barrier(2)
        ids: list[str] = []
        errors: list[Exception] = []

        def execute() -> None:
            try:
                barrier.wait(timeout=5)
                ids.append(self.create("supplier-page-concurrent").id)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=execute) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertFalse(errors)
        self.assertEqual(len(set(ids)), 1)
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(AssetIngestionModel)), 1)
            self.assertEqual(session.scalar(select(func.count()).select_from(ProcessingJobModel)), 2)

    def test_item_status_rolls_up_to_ingestion(self) -> None:
        ingestion = self.create("supplier-page-status")
        with self.sessions() as session:
            repository = ExternalIngestionRepository(session)
            items = repository.list_items(
                tenant_id="tenant-a", ingestion_id=ingestion.id, limit=10, offset=0
            )
            repository.update_item_status(
                tenant_id="tenant-a",
                ingestion_id=ingestion.id,
                item_id=items[0].id,
                status="completed",
            )
            self.assertEqual(session.get(AssetIngestionModel, ingestion.id).status, "processing")
            repository.update_item_status(
                tenant_id="tenant-a",
                ingestion_id=ingestion.id,
                item_id=items[1].id,
                status="failed",
                error_code="download_failed",
                error_message="supplier timeout",
            )
            self.assertEqual(session.get(AssetIngestionModel, ingestion.id).status, "partial_failed")


if __name__ == "__main__":
    unittest.main()
