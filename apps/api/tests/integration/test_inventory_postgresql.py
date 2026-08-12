from __future__ import annotations

import os
import asyncio
import tempfile
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PIL import Image
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

from sqlalchemy import create_engine, delete, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.assets.model import AssetModel, ExternalSourceModel, SourceAssetModel
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.auth_persistence.model import TenantModel
from app.modules.inventory.ai.gateway import InventoryAiGatewayResult
from app.modules.inventory.ai.service import (
    INVENTORY_DOCUMENT_ANALYZE_JOB,
    InventoryAnalyzeFailure,
    InventoryDocumentAnalyzer,
)
from app.modules.inventory.persistence_model import (
    InventoryAiAnalysisModel,
    InventoryDocumentModel,
    InventoryDocumentPageModel,
    InventoryItemAliasModel,
    InventoryItemModel,
    InventoryLocationModel,
    InventoryLineModel,
    InventoryReviewEventModel,
    InventoryReviewModel,
    InventoryTransactionModel,
)
from app.modules.inventory.jobs.repository import InventoryJobRepository
from app.modules.inventory.documents.service import (
    INVENTORY_DOCUMENT_NORMALIZE_JOB, INVENTORY_DOCUMENT_VALIDATE_JOB, InventoryBusinessFailure,
    InventoryDocumentNormalizer, InventoryDocumentValidator,
)
from app.modules.inventory.review.service import InventoryReviewService
from app.modules.inventory.daily.service import InventoryDailyRunService
from app.modules.inventory.daily.scheduler import InventoryDailyScheduler
from app.modules.inventory.transactions.service import INVENTORY_DOCUMENT_COMMIT_JOB, InventoryDocumentCommitter
from app.modules.explorer.schema import AssetNode
from app.modules.inventory.drive.downloader import InventoryFileDownloader
from app.modules.inventory.drive.poller import InventoryDrivePoller
from app.modules.inventory.drive.storage import InventorySourceStorage
from app.modules.inventory.preparation.image import InventoryImagePreparationLimits, StatelessInventoryImagePreparer
from app.modules.inventory.preparation.service import INVENTORY_DOCUMENT_PREPARE_JOB, InventoryDocumentPreparer
from app.modules.inventory.preparation.storage import InventoryPreparedStorage
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.model import (
    InventoryAiControlModel,
    InventoryProcessingControlModel,
)
from app.modules.inventory.persistence_model import InventorySettingsModel, InventorySourceFileModel, InventoryDailyRunEventModel, InventoryDailyRunModel
from app.modules.pipeline.model import AssetPipelineModel

from app.modules.inventory.repository import InventoryCatalogRepository, InventorySourceFileRepository
from app.modules.inventory.schema import InventorySourceFileInput
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.search.governance_model import SearchIndexRecordModel
from tests.modules.inventory.test_persistence import PHASE2_TABLES


DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL") or os.getenv("DATABASE_URL", "")
POSTGRES_AVAILABLE = DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg://"))


@unittest.skipUnless(POSTGRES_AVAILABLE, "PostgreSQL integration database is not configured")
class InventoryPostgreSqlIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        cls.sessions = sessionmaker(cls.engine, class_=Session, expire_on_commit=False)
        if cls.engine.dialect.name != "postgresql":
            raise RuntimeError("Inventory integration tests require PostgreSQL")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def test_phase2_tables_exist_after_migration(self) -> None:
        self.assertTrue(PHASE2_TABLES <= set(inspect(self.engine).get_table_names()))

    def test_provider_identity_and_cross_tenant_fk_on_postgresql(self) -> None:
        marker = uuid4().hex
        tenant_a, tenant_b = f"inv-a-{marker}", f"inv-b-{marker}"
        source_a, source_b = f"sa-{marker}", f"sb-{marker}"
        with self.sessions() as session:
            session.add_all(
                [
                    TenantModel(id=tenant_a, name="A", slug=tenant_a),
                    TenantModel(id=tenant_b, name="B", slug=tenant_b),
                    ExternalSourceModel(id=source_a, tenant_id=tenant_a, source_key=source_a, source_type="google_drive"),
                    ExternalSourceModel(id=source_b, tenant_id=tenant_b, source_key=source_b, source_type="google_drive"),
                ]
            )
            session.commit()
            catalog = InventoryCatalogRepository(session)
            location_a = catalog.create_location(tenant_a, code="MAIN", name="A")
            location_b = catalog.create_location(tenant_b, code="MAIN", name="B")
            value = InventorySourceFileInput(
                external_source_id=source_a, drive_file_id="same-file",
                filename="count.jpg", mime_type="image/jpeg",
                drive_modified_time=datetime.now(timezone.utc),
            )
            files = InventorySourceFileRepository(session)
            file_a = files.register(tenant_a, value)
            file_b = files.register(
                tenant_b, value.model_copy(update={"external_source_id": source_b})
            )
            session.commit()
            self.assertNotEqual(location_a.id, location_b.id)
            self.assertNotEqual(file_a.id, file_b.id)
            self.assertEqual(file_a.id, files.register(tenant_a, value).id)

        with self.sessions() as session:
            session.add(
                InventoryDocumentModel(
                    tenant_id=tenant_b, idempotency_key=f"cross-{marker}",
                    business_date=date.today(), document_type="stock_count",
                    location_id=location_a.id,
                )
            )
            with self.assertRaises(IntegrityError):
                session.flush()
    def test_concurrent_provider_and_job_registration_are_idempotent(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        marker = uuid4().hex
        tenant_id = f"inv-race-{marker}"
        source_id = f"s-{marker}"
        with self.sessions() as session:
            session.add_all([
                TenantModel(id=tenant_id, name="Race", slug=tenant_id),
                ExternalSourceModel(
                    id=source_id, tenant_id=tenant_id, source_key=source_id,
                    source_type="google_drive",
                ),
            ])
            session.commit()

        value = InventorySourceFileInput(
            external_source_id=source_id,
            drive_file_id="same-file",
            filename="count.jpg",
            mime_type="image/jpeg",
            drive_modified_time=datetime.now(timezone.utc),
        )

        def register_file() -> str:
            with self.sessions() as session:
                row = InventorySourceFileRepository(session).register(
                    tenant_id, value
                )
                session.commit()
                return row.id

        with ThreadPoolExecutor(max_workers=2) as executor:
            file_ids = list(executor.map(lambda _index: register_file(), range(2)))
        self.assertEqual(len(set(file_ids)), 1)

        def register_job() -> str:
            with self.sessions() as session:
                row = InventoryJobRepository(
                    session, ("inventory_file_download",)
                ).create_job(
                    tenant_id=tenant_id,
                    job_type="inventory_file_download",
                    entity_type="inventory_source_file",
                    entity_id=file_ids[0],
                    idempotency_key=f"inventory-file-download:{file_ids[0]}",
                )
                session.commit()
                return row.id

        with ThreadPoolExecutor(max_workers=2) as executor:
            job_ids = list(executor.map(lambda _index: register_job(), range(2)))
        self.assertEqual(len(set(job_ids)), 1)
    def test_concurrent_pollers_create_one_provider_version_and_job(self) -> None:
        marker = uuid4().hex
        tenant_id = f"inv-poller-{marker}"
        source_id = f"s-{marker[:30]}"
        modified = datetime.now(timezone.utc).replace(microsecond=0)
        with self.sessions() as session:
            session.add_all([
                TenantModel(id=tenant_id, name="Poller race", slug=tenant_id),
                ExternalSourceModel(
                    id=source_id,
                    tenant_id=tenant_id,
                    source_key=source_id,
                    source_type="google_drive",
                    source_metadata={"oauth_connection_id": f"oauth-{marker}"},
                ),
                InventoryProcessingControlModel(
                    tenant_id=tenant_id,
                    enabled=True,
                    paused=False,
                    max_active_jobs=2,
                    max_ai_jobs=0,
                ),
                InventorySettingsModel(
                    tenant_id=tenant_id,
                    enabled=True,
                    external_source_id=source_id,
                    inbox_folder_id=f"inbox-{marker}",
                    drive_poll_interval_seconds=60,
                ),
            ])
            session.commit()

        barrier = Barrier(2)

        class ConcurrentDrive:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def children_page(self, parent_id, **_kwargs):
                if parent_id != f"inbox-{marker}":
                    raise AssertionError("Poller used the wrong Inbox folder")
                barrier.wait(timeout=10)
                return (
                    [
                        AssetNode(
                            id="same-drive-file",
                            name="count.jpg",
                            kind="image",
                            mime_type="image/jpeg",
                            modified_at=modified,
                            size=4,
                        )
                    ],
                    None,
                )

        async def token(_connection_id):
            return "mock-access"

        def run_poller():
            with self.sessions() as session:
                binding = session.scalar(
                    select(InventorySettingsModel).where(
                        InventorySettingsModel.tenant_id == tenant_id
                    )
                )
                poller = InventoryDrivePoller(
                    session,
                    automation_enabled=True,
                    poller_enabled=True,
                    token_resolver=token,
                    client_factory=lambda _access: ConcurrentDrive(),
                )
                summary = asyncio.run(poller.poll_binding(binding))
                session.commit()
                return summary

        with ThreadPoolExecutor(max_workers=2) as executor:
            summaries = list(executor.map(lambda _index: run_poller(), range(2)))

        self.assertEqual(len(summaries), 2)
        with self.sessions() as session:
            self.assertEqual(
                session.scalar(
                    select(func.count(InventorySourceFileModel.id)).where(
                        InventorySourceFileModel.tenant_id == tenant_id
                    )
                ),
                1,
            )
            self.assertEqual(
                session.scalar(
                    select(func.count(InventoryJobModel.id)).where(
                        InventoryJobModel.tenant_id == tenant_id,
                        InventoryJobModel.job_type == "inventory_file_download",
                    )
                ),
                1,
            )
            binding = session.scalar(
                select(InventorySettingsModel).where(
                    InventorySettingsModel.tenant_id == tenant_id
                )
            )
            self.assertIsNone(binding.last_poll_error_code)

    def test_inventory_pause_blocks_claim_until_resumed(self) -> None:
        marker = uuid4().hex
        tenant_id = f"inv-pause-{marker}"
        with self.sessions() as session:
            session.add_all([
                TenantModel(id=tenant_id, name="Paused", slug=tenant_id),
                InventoryProcessingControlModel(
                    tenant_id=tenant_id,
                    enabled=True,
                    paused=True,
                    max_active_jobs=1,
                    max_ai_jobs=0,
                ),
            ])
            repository = InventoryJobRepository(
                session, ("inventory_file_download",)
            )
            next_priority = 1 + session.scalar(
                select(func.coalesce(func.max(InventoryJobModel.priority), 0))
            )
            job = repository.create_job(
                tenant_id=tenant_id,
                job_type="inventory_file_download",
                entity_type="inventory_source_file",
                entity_id=f"source-file-{marker}",
                idempotency_key=f"pause-{marker}",
                priority=next_priority,
            )
            session.commit()
            claimed_while_paused = repository.claim_next(
                worker_id="inventory-worker",
                lease_seconds=60,
            )
            self.assertNotEqual(
                getattr(claimed_while_paused, "id", None), job.id
            )
            control = session.scalar(
                select(InventoryProcessingControlModel).where(
                    InventoryProcessingControlModel.tenant_id == tenant_id
                )
            )
            control.paused = False
            session.commit()
            claimed = repository.claim_next(
                worker_id="inventory-worker",
                lease_seconds=60,
            )
            self.assertEqual(claimed.id, job.id)

    def test_creative_pause_does_not_stop_inventory_poller(self) -> None:
        marker = uuid4().hex
        tenant_id = f"inv-creative-pause-{marker}"
        source_id = f"s-{marker[:30]}"
        modified = datetime.now(timezone.utc).replace(microsecond=0)
        with self.sessions() as session:
            session.add_all([
                TenantModel(id=tenant_id, name="Creative paused", slug=tenant_id),
                ExternalSourceModel(
                    id=source_id,
                    tenant_id=tenant_id,
                    source_key=source_id,
                    source_type="google_drive",
                    source_metadata={"oauth_connection_id": f"oauth-{marker}"},
                ),
                InventoryProcessingControlModel(
                    tenant_id=tenant_id,
                    enabled=True,
                    paused=False,
                    max_active_jobs=1,
                    max_ai_jobs=0,
                ),
                InventorySettingsModel(
                    tenant_id=tenant_id,
                    enabled=True,
                    external_source_id=source_id,
                    inbox_folder_id=f"inbox-{marker}",
                    drive_poll_interval_seconds=60,
                ),
                TenantProcessingPolicyModel(
                    tenant_id=tenant_id,
                    pipeline_enabled=True,
                    processing_paused=True,
                ),
            ])
            session.commit()

        calls = []

        class Drive:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def children_page(self, parent_id, **_kwargs):
                calls.append(parent_id)
                return (
                    [
                        AssetNode(
                            id="creative-pause-file",
                            name="count.png",
                            kind="image",
                            mime_type="image/png",
                            modified_at=modified,
                        )
                    ],
                    None,
                )

        async def token(_connection_id):
            return "mock-access"

        with self.sessions() as session:
            binding = session.scalar(
                select(InventorySettingsModel).where(
                    InventorySettingsModel.tenant_id == tenant_id
                )
            )
            poller = InventoryDrivePoller(
                session,
                automation_enabled=True,
                poller_enabled=True,
                token_resolver=token,
                client_factory=lambda _access: Drive(),
            )
            summary = asyncio.run(poller.poll_binding(binding))
            session.commit()
            self.assertEqual(calls, [f"inbox-{marker}"])
            self.assertEqual(summary.jobs_created, 1)
            self.assertEqual(
                session.scalar(
                    select(func.count(InventorySourceFileModel.id)).where(
                        InventorySourceFileModel.tenant_id == tenant_id
                    )
                ),
                1,
            )
            policy = session.get(TenantProcessingPolicyModel, tenant_id)
            self.assertTrue(policy.processing_paused)

    def test_poll_and_download_leave_all_creative_tables_unchanged(self) -> None:
        marker = uuid4().hex
        tenant_id = f"inv-isolation-{marker}"
        source_id = f"s-{marker[:30]}"
        modified = datetime.now(timezone.utc).replace(microsecond=0)
        with self.sessions() as session:
            session.add_all([
                TenantModel(id=tenant_id, name="Isolation", slug=tenant_id),
                ExternalSourceModel(
                    id=source_id,
                    tenant_id=tenant_id,
                    source_key=source_id,
                    source_type="google_drive",
                    source_metadata={"oauth_connection_id": f"oauth-{marker}"},
                ),
                InventoryProcessingControlModel(
                    tenant_id=tenant_id,
                    enabled=True,
                    paused=False,
                    max_active_jobs=1,
                    max_ai_jobs=0,
                ),
                InventorySettingsModel(
                    tenant_id=tenant_id,
                    enabled=True,
                    external_source_id=source_id,
                    inbox_folder_id=f"inbox-{marker}",
                    drive_poll_interval_seconds=60,
                ),
            ])
            creative_models = (
                SourceAssetModel,
                AssetModel,
                AssetPipelineModel,
                AssetAiAnalysisModel,
                SearchIndexRecordModel,
            )
            before = {
                model.__tablename__: session.scalar(
                    select(func.count()).select_from(model)
                )
                for model in creative_models
            }
            inventory_before = (
                session.scalar(select(func.count()).select_from(InventorySourceFileModel)),
                session.scalar(select(func.count()).select_from(InventoryJobModel)),
            )
            session.commit()

        node = AssetNode(
            id="isolated-file",
            name="isolated.avif",
            kind="image",
            mime_type="image/avif",
            modified_at=modified,
        )

        class Drive:
            def __init__(self, *, listing):
                self.listing = listing

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def children_page(self, _parent_id, **_kwargs):
                return ([node], None)

            async def get(self, _item_id):
                return node

        async def token(_connection_id):
            return "mock-access"

        async def open_stream(_token, _item_id, _range):
            class Response:
                async def aiter_bytes(self):
                    yield b"inventory-only-bytes"

            return object(), Response()

        async def close_stream(_client, _response):
            return None

        with tempfile.TemporaryDirectory() as directory:
            with self.sessions() as session:
                binding = session.scalar(
                    select(InventorySettingsModel).where(
                        InventorySettingsModel.tenant_id == tenant_id
                    )
                )
                poller = InventoryDrivePoller(
                    session,
                    automation_enabled=True,
                    poller_enabled=True,
                    token_resolver=token,
                    client_factory=lambda _access: Drive(listing=True),
                )
                asyncio.run(poller.poll_binding(binding))
                session.commit()
                job = session.scalar(
                    select(InventoryJobModel).where(
                        InventoryJobModel.tenant_id == tenant_id
                    )
                )
            downloader = InventoryFileDownloader(
                self.sessions,
                storage=InventorySourceStorage(directory),
                max_bytes=1024,
                token_resolver=token,
                client_factory=lambda _access: Drive(listing=False),
                stream_opener=open_stream,
                stream_closer=close_stream,
            )
            asyncio.run(downloader.execute(job))

            with self.sessions() as session:
                after = {
                    model.__tablename__: session.scalar(
                        select(func.count()).select_from(model)
                    )
                    for model in creative_models
                }
                self.assertEqual(after, before)
                self.assertEqual(
                    session.scalar(select(func.count()).select_from(InventorySourceFileModel)),
                    inventory_before[0] + 1,
                )
                self.assertEqual(
                    session.scalar(select(func.count()).select_from(InventoryJobModel)),
                    inventory_before[1] + 2,
                )
                self.assertEqual(
                    set(session.scalars(select(InventoryJobModel.job_type).where(InventoryJobModel.tenant_id == tenant_id))),
                    {"inventory_file_download", "inventory_document_prepare"},
                )
                source_file = session.scalar(
                    select(InventorySourceFileModel).where(
                        InventorySourceFileModel.tenant_id == tenant_id
                    )
                )
                self.assertEqual(source_file.status, "downloaded")
                self.assertTrue((Path(directory) / source_file.storage_key).is_file())

    def test_concurrent_preparation_creates_one_document_and_page(self) -> None:
        marker = uuid4().hex
        tenant_id, source_id, file_id = f"inv-prep-{marker}", f"src-{marker[:28]}", f"file-{marker[:27]}"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "source.jpg"
            Image.new("RGB", (120, 60), "blue").save(image_path, format="JPEG")
            content = image_path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            storage_key = f"inventory/{tenant_id}/source/{file_id}/{digest}.jpg"
            target = root / storage_key
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            with self.sessions() as session:
                session.add_all((
                    TenantModel(id=tenant_id, name="Preparation", slug=tenant_id),
                    ExternalSourceModel(id=source_id, tenant_id=tenant_id, source_key=source_id, source_type="google_drive"),
                    InventorySourceFileModel(
                        id=file_id, tenant_id=tenant_id, external_source_id=source_id,
                        drive_file_id="drive-prep", filename="count.jpg", mime_type="image/jpeg",
                        drive_modified_time=datetime.now(timezone.utc), drive_size=len(content),
                        content_sha256=digest, storage_key=storage_key, status="downloaded",
                    ),
                ))
                session.commit()
            limits = InventoryImagePreparationLimits(1_000_000, 1000, 1000, 1_000_000, 1_000_000, 64, 64, 85)
            barrier = Barrier(2)
            def prepare() -> None:
                worker = InventoryDocumentPreparer(
                    self.sessions,
                    source_storage=InventoryPreparedStorage(root),
                    prepared_storage=InventoryPreparedStorage(root),
                    image_preparer=StatelessInventoryImagePreparer(limits),
                )
                barrier.wait()
                worker.execute(InventoryJobModel(
                    tenant_id=tenant_id, job_type=INVENTORY_DOCUMENT_PREPARE_JOB,
                    entity_type="inventory_source_file", entity_id=file_id,
                    idempotency_key=f"inventory-document-prepare:v1:{file_id}",
                    payload_json={"source_file_id": file_id},
                ))
            with ThreadPoolExecutor(max_workers=2) as executor:
                list(executor.map(lambda _value: prepare(), range(2)))
            with self.sessions() as session:
                self.assertEqual(session.scalar(select(func.count(InventoryDocumentModel.id)).where(InventoryDocumentModel.tenant_id == tenant_id)), 1)
                self.assertEqual(session.scalar(select(func.count(InventoryDocumentPageModel.id)).where(InventoryDocumentPageModel.tenant_id == tenant_id)), 1)
                source = session.get(InventorySourceFileModel, file_id)
                self.assertEqual(source.preparation_status, "prepared")

    def test_preparation_does_not_create_creative_rows(self) -> None:
        marker = uuid4().hex
        tenant_id, source_id, file_id = f"inv-prep-iso-{marker}", f"src-{marker[:28]}", f"file-{marker[:27]}"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "source.jpg"
            Image.new("RGB", (20, 10), "green").save(payload, format="JPEG")
            content = payload.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            storage_key = f"inventory/{tenant_id}/source/{file_id}/{digest}.jpg"
            target = root / storage_key
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            with self.sessions() as session:
                before = tuple(session.scalar(select(func.count(model.id))) for model in (SourceAssetModel, AssetModel, AssetPipelineModel, AssetAiAnalysisModel, SearchIndexRecordModel))
                session.add_all((
                    TenantModel(id=tenant_id, name="Isolation", slug=tenant_id),
                    ExternalSourceModel(id=source_id, tenant_id=tenant_id, source_key=source_id, source_type="google_drive"),
                    InventorySourceFileModel(id=file_id, tenant_id=tenant_id, external_source_id=source_id, drive_file_id="drive-isolation", filename="count.jpg", mime_type="image/jpeg", drive_modified_time=datetime.now(timezone.utc), drive_size=len(content), content_sha256=digest, storage_key=storage_key, status="downloaded"),
                ))
                session.commit()
            InventoryDocumentPreparer(self.sessions, source_storage=InventoryPreparedStorage(root), prepared_storage=InventoryPreparedStorage(root), image_preparer=StatelessInventoryImagePreparer(InventoryImagePreparationLimits(1_000_000, 1000, 1000, 1_000_000, 1_000_000, 64, 64, 85))).execute(InventoryJobModel(tenant_id=tenant_id, job_type=INVENTORY_DOCUMENT_PREPARE_JOB, entity_type="inventory_source_file", entity_id=file_id, idempotency_key=f"inventory-document-prepare:v1:{file_id}", payload_json={"source_file_id": file_id}))
            with self.sessions() as session:
                after = tuple(session.scalar(select(func.count(model.id))) for model in (SourceAssetModel, AssetModel, AssetPipelineModel, AssetAiAnalysisModel, SearchIndexRecordModel))
                self.assertEqual(after, before)


    def test_phase5_concurrent_analysis_is_idempotent_and_tenant_scoped(self) -> None:
        marker = uuid4().hex
        tenant_a, tenant_b = f"inv-ai-a-{marker}", f"inv-ai-b-{marker}"
        source_a, source_b = f"src-ai-a-{marker[:24]}", f"src-ai-b-{marker[:24]}"
        page_a, page_b = str(uuid4()), str(uuid4())
        content = b"prepared-inventory-page"
        digest = hashlib.sha256(content).hexdigest()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def add_page(session, tenant_id, source_id, page_id):
                key = f"inventory/{tenant_id}/prepared/{page_id}/{digest}.jpg"
                target = root / key
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                source = InventorySourceFileModel(
                    id=str(uuid4()), tenant_id=tenant_id,
                    external_source_id=source_id, drive_file_id=f"drive-{page_id}",
                    filename="count.jpg", mime_type="image/jpeg",
                    drive_modified_time=datetime.now(timezone.utc), status="downloaded",
                    content_sha256=digest,
                )
                document = InventoryDocumentModel(
                    id=str(uuid4()), tenant_id=tenant_id,
                    idempotency_key=f"doc-{page_id}", document_type="unclassified",
                    status="prepared", expected_pages=1, received_pages=1,
                )
                page = InventoryDocumentPageModel(
                    id=page_id, tenant_id=tenant_id, document_id=document.id,
                    source_file_id=source.id, drive_file_id=source.drive_file_id,
                    page_number=1, page_count=1, content_sha256=digest,
                    preparation_status="prepared", prepared_storage_key=key,
                    prepared_content_sha256=digest, prepared_mime_type="image/jpeg",
                )
                session.add_all((source, document))
                session.flush()
                session.add(page)

            with self.sessions() as session:
                session.add_all((
                    TenantModel(id=tenant_a, name="AI tenant A", slug=tenant_a),
                    TenantModel(id=tenant_b, name="AI tenant B", slug=tenant_b),
                    ExternalSourceModel(id=source_a, tenant_id=tenant_a, source_key=source_a, source_type="google_drive"),
                    ExternalSourceModel(id=source_b, tenant_id=tenant_b, source_key=source_b, source_type="google_drive"),
                    InventoryAiControlModel(tenant_id=tenant_a, enabled=True, provider="fake", allowed_models_json=["fake-v1"], max_concurrent=1, min_start_interval_seconds=0, per_run_limit=1),
                    InventoryAiControlModel(tenant_id=tenant_b, enabled=True, provider="fake", allowed_models_json=["fake-v1"], max_concurrent=1, min_start_interval_seconds=0, per_run_limit=1),
                ))
                add_page(session, tenant_a, source_a, page_a)
                add_page(session, tenant_b, source_b, page_b)
                session.commit()

            class Gateway:
                def __init__(self):
                    self.calls = 0
                def analyze(self, **_kwargs):
                    self.calls += 1
                    extracted = {"document_type": "stock_count", "business_date": None, "location": None, "page_number": 1, "page_count": 1, "raw_item_lines": []}
                    return InventoryAiGatewayResult(raw_response_json={"candidate": extracted}, extracted_json=extracted, provider_request_id="request-id", usage_json={"input_tokens": 1}, estimated_cost_micros=1)

            gateway = Gateway()
            def execute() -> str:
                analyzer = InventoryDocumentAnalyzer(self.sessions, prepared_storage=InventoryPreparedStorage(root), gateway=gateway, enabled=True)
                job = InventoryJobModel(tenant_id=tenant_a, job_type=INVENTORY_DOCUMENT_ANALYZE_JOB, entity_type="inventory_document_page", entity_id=page_a, idempotency_key=f"job-{page_a}", payload_json={"page_id": page_a})
                try:
                    analyzer.execute(job)
                    return "completed"
                except InventoryAnalyzeFailure as exc:
                    self.assertEqual(exc.code, "inventory_ai_concurrency_limited")
                    self.assertTrue(exc.retryable)
                    return "retryable"

            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(lambda _value: execute(), range(2)))
            self.assertIn("completed", outcomes)
            with self.sessions() as session:
                self.assertEqual(session.scalar(select(func.count(InventoryAiAnalysisModel.id)).where(InventoryAiAnalysisModel.tenant_id == tenant_a)), 1)
                analysis = session.scalar(select(InventoryAiAnalysisModel).where(InventoryAiAnalysisModel.tenant_id == tenant_a))
                self.assertEqual(analysis.status, "succeeded")
                self.assertEqual(session.scalar(select(func.count(InventoryAiAnalysisModel.id)).where(InventoryAiAnalysisModel.tenant_id == tenant_b)), 0)
            self.assertEqual(gateway.calls, 1)

            analyzer = InventoryDocumentAnalyzer(self.sessions, prepared_storage=InventoryPreparedStorage(root), gateway=gateway, enabled=True)
            foreign_job = InventoryJobModel(tenant_id=tenant_a, job_type=INVENTORY_DOCUMENT_ANALYZE_JOB, entity_type="inventory_document_page", entity_id=page_b, idempotency_key="foreign-page", payload_json={"page_id": page_b})
            with self.assertRaises(InventoryAnalyzeFailure) as raised:
                analyzer.execute(foreign_job)
            self.assertEqual(raised.exception.code, "inventory_ai_page_not_found")
            self.assertEqual(gateway.calls, 1)

    def test_phase5_controls_enforce_rate_budgets_stop_and_creative_isolation(self) -> None:
        marker = uuid4().hex
        tenant_id, source_id = f"inv-ai-controls-{marker}", f"src-ai-controls-{marker[:20]}"
        content = b"prepared-controls-page"
        digest = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.sessions() as session:
                session.add_all((
                    TenantModel(id=tenant_id, name="AI controls", slug=tenant_id),
                    ExternalSourceModel(id=source_id, tenant_id=tenant_id, source_key=source_id, source_type="google_drive"),
                    InventoryAiControlModel(tenant_id=tenant_id, enabled=True, provider="fake", allowed_models_json=["fake-v1"], max_concurrent=1, min_start_interval_seconds=0, per_run_limit=1),
                ))
                creative_before = session.scalar(select(func.count(AssetAiAnalysisModel.id)))
                session.commit()

            def create_page(suffix):
                page_id = str(uuid4())
                key = f"inventory/{tenant_id}/prepared/{page_id}/{digest}.jpg"
                target = root / key
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                with self.sessions() as session:
                    source = InventorySourceFileModel(id=str(uuid4()), tenant_id=tenant_id, external_source_id=source_id, drive_file_id=f"drive-{page_id}", filename="count.jpg", mime_type="image/jpeg", drive_modified_time=datetime.now(timezone.utc), status="downloaded", content_sha256=digest)
                    document = InventoryDocumentModel(id=str(uuid4()), tenant_id=tenant_id, idempotency_key=f"doc-{page_id}", document_type="unclassified", status="prepared", expected_pages=1, received_pages=1)
                    page = InventoryDocumentPageModel(id=page_id, tenant_id=tenant_id, document_id=document.id, source_file_id=source.id, drive_file_id=source.drive_file_id, page_number=1, page_count=1, content_sha256=digest, preparation_status="prepared", prepared_storage_key=key, prepared_content_sha256=digest, prepared_mime_type="image/jpeg")
                    session.add_all((source, document)); session.flush(); session.add(page); session.commit()
                return page_id

            class Gateway:
                def __init__(self): self.calls = 0
                def analyze(self, **_kwargs):
                    self.calls += 1
                    extracted = {"document_type": "stock_count", "business_date": None, "location": None, "page_number": 1, "page_count": 1, "raw_item_lines": []}
                    return InventoryAiGatewayResult(raw_response_json={"candidate": extracted}, extracted_json=extracted, provider_request_id="request-id", usage_json={}, estimated_cost_micros=10)

            gateway = Gateway()
            analyzer = InventoryDocumentAnalyzer(self.sessions, prepared_storage=InventoryPreparedStorage(root), gateway=gateway, enabled=True, estimated_cost_micros=10)
            def job(page_id):
                return InventoryJobModel(tenant_id=tenant_id, job_type=INVENTORY_DOCUMENT_ANALYZE_JOB, entity_type="inventory_document_page", entity_id=page_id, idempotency_key=f"job-{page_id}", payload_json={"page_id": page_id})

            first = create_page("first")
            analyzer.execute(job(first))
            self.assertEqual(gateway.calls, 1)
            with self.sessions() as session:
                control = session.scalar(select(InventoryAiControlModel).where(InventoryAiControlModel.tenant_id == tenant_id))
                control.min_start_interval_seconds = 3600
                session.commit()
            with self.assertRaises(InventoryAnalyzeFailure) as raised:
                analyzer.execute(job(create_page("rate")))
            self.assertEqual(raised.exception.code, "inventory_ai_rate_limited")

            with self.sessions() as session:
                control = session.scalar(select(InventoryAiControlModel).where(InventoryAiControlModel.tenant_id == tenant_id))
                control.min_start_interval_seconds = 0; control.daily_budget_micros = 10
                session.commit()
            with self.assertRaises(InventoryAnalyzeFailure) as raised:
                analyzer.execute(job(create_page("daily")))
            self.assertEqual(raised.exception.code, "inventory_ai_daily_budget_exceeded")

            with self.sessions() as session:
                control = session.scalar(select(InventoryAiControlModel).where(InventoryAiControlModel.tenant_id == tenant_id))
                control.daily_budget_micros = 0; control.monthly_budget_micros = 10
                session.commit()
            with self.assertRaises(InventoryAnalyzeFailure) as raised:
                analyzer.execute(job(create_page("monthly")))
            self.assertEqual(raised.exception.code, "inventory_ai_monthly_budget_exceeded")

            with self.sessions() as session:
                control = session.scalar(select(InventoryAiControlModel).where(InventoryAiControlModel.tenant_id == tenant_id))
                control.monthly_budget_micros = 0; control.emergency_stop = True
                session.commit()
            with self.assertRaises(InventoryAnalyzeFailure) as raised:
                analyzer.execute(job(create_page("stop")))
            self.assertEqual(raised.exception.code, "inventory_ai_emergency_stop")
            self.assertEqual(gateway.calls, 1)
            with self.sessions() as session:
                self.assertEqual(session.scalar(select(func.count(AssetAiAnalysisModel.id))), creative_before)


@unittest.skipUnless(POSTGRES_AVAILABLE, "PostgreSQL integration database is not configured")
class InventoryPhase6PostgreSqlIntegrationTest(unittest.TestCase):
    """Phase 6 acceptance uses real PostgreSQL sessions and constraints."""
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        cls.sessions = sessionmaker(cls.engine, class_=Session, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def _fixture(self, *, low_confidence=False):
        marker = uuid4().hex
        tenant, other, source = f"p6-{marker}", f"p6-other-{marker}", f"src-{marker[:28]}"
        ids = {key: str(uuid4()) for key in ("file", "doc", "page", "analysis", "item", "other_item")}
        raw = {"raw_item_lines": [{"raw_item_name": "Coffee beans", "whole_quantity": "2", "fraction_quantity": "0.5", "whole_unit": "kg", "fraction_unit": "kg", "confidence": "0.70" if low_confidence else "0.95"}]}
        with self.sessions() as session:
            session.add_all((
                TenantModel(id=tenant, name="Phase6", slug=tenant), TenantModel(id=other, name="Other", slug=other),
                ExternalSourceModel(id=source, tenant_id=tenant, source_key=source, source_type="google_drive"),
                InventoryProcessingControlModel(tenant_id=tenant, enabled=True, paused=False, max_active_jobs=4, max_ai_jobs=0),
                InventorySourceFileModel(id=ids["file"], tenant_id=tenant, external_source_id=source, drive_file_id=f"drive-{marker}", filename="count.jpg", mime_type="image/jpeg", drive_modified_time=datetime.now(timezone.utc), status="downloaded"),
                InventoryDocumentModel(id=ids["doc"], tenant_id=tenant, idempotency_key=f"doc-{marker}", document_type="stock_count", status="prepared", expected_pages=1, received_pages=1),
                InventoryItemModel(id=ids["item"], tenant_id=tenant, sku=f"sku-{marker}", name="Coffee beans", base_unit="kg", conversion_factor=1),
                InventoryItemModel(id=ids["other_item"], tenant_id=other, sku=f"other-{marker}", name="Other beans", base_unit="kg", conversion_factor=1),
            ))
            session.flush()
            session.add(InventoryDocumentPageModel(id=ids["page"], tenant_id=tenant, document_id=ids["doc"], source_file_id=ids["file"], drive_file_id=f"drive-{marker}", page_number=1, page_count=1, preparation_status="prepared"))
            # PostgreSQL enforces the page composite FK immediately; flush its
            # parent row before adding the analysis fixture.
            session.flush()
            session.add(InventoryAiAnalysisModel(id=ids["analysis"], tenant_id=tenant, document_id=ids["doc"], page_id=ids["page"], analysis_version=1, idempotency_key=f"analysis-{marker}", provider="fake", model="fake", prompt_version="v1", schema_version="v1", status="succeeded", confidence=0.95, raw_result_json={"immutable": True}, extracted_json=raw))
            session.commit()
        return tenant, other, ids, raw

    @staticmethod
    def _normalize_job(tenant, analysis):
        return InventoryJobModel(tenant_id=tenant, job_type=INVENTORY_DOCUMENT_NORMALIZE_JOB, entity_type="inventory_ai_analysis", entity_id=analysis, idempotency_key=f"normalize:{analysis}")

    @staticmethod
    def _validate_job(tenant, doc):
        return InventoryJobModel(tenant_id=tenant, job_type=INVENTORY_DOCUMENT_VALIDATE_JOB, entity_type="inventory_document", entity_id=doc, idempotency_key=f"validate:{doc}", payload_json={"document_id": doc})

    def test_concurrent_normalize_is_idempotent(self):
        tenant, _other, ids, raw = self._fixture()
        barrier = Barrier(2)
        def execute():
            barrier.wait(timeout=10)
            InventoryDocumentNormalizer(self.sessions).execute(self._normalize_job(tenant, ids["analysis"]))
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _x: execute(), range(2)))
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(func.count(InventoryLineModel.id)).where(InventoryLineModel.tenant_id == tenant)), 1)
            analysis = session.get(InventoryAiAnalysisModel, ids["analysis"])
            self.assertEqual(analysis.raw_result_json, {"immutable": True})
            self.assertEqual(session.scalar(select(func.count(InventoryJobModel.id)).where(InventoryJobModel.tenant_id == tenant, InventoryJobModel.job_type == INVENTORY_DOCUMENT_VALIDATE_JOB)), 1)

    def test_concurrent_validate_is_idempotent_and_creates_no_transactions(self):
        tenant, _other, ids, _raw = self._fixture(low_confidence=True)
        InventoryDocumentNormalizer(self.sessions).execute(self._normalize_job(tenant, ids["analysis"]))
        barrier = Barrier(2)
        def execute():
            barrier.wait(timeout=10)
            InventoryDocumentValidator(self.sessions).execute(self._validate_job(tenant, ids["doc"]))
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _x: execute(), range(2)))
        with self.sessions() as session:
            self.assertEqual(session.get(InventoryDocumentModel, ids["doc"]).status, "needs_review")
            self.assertEqual(session.scalar(select(func.count(InventoryReviewModel.id)).where(InventoryReviewModel.tenant_id == tenant)), 1)
            self.assertEqual(session.scalar(select(func.count(InventoryTransactionModel.id)).where(InventoryTransactionModel.tenant_id == tenant)), 0)

    def test_concurrent_review_mutations_are_safe_and_events_append(self):
        tenant, _other, ids, _raw = self._fixture(low_confidence=True)
        InventoryDocumentNormalizer(self.sessions).execute(self._normalize_job(tenant, ids["analysis"]))
        InventoryDocumentValidator(self.sessions).execute(self._validate_job(tenant, ids["doc"]))
        with self.sessions() as session:
            review_id = session.scalar(select(InventoryReviewModel.id).where(InventoryReviewModel.tenant_id == tenant))
        barrier = Barrier(2)
        def approve():
            barrier.wait(timeout=10); InventoryReviewService(self.sessions).mutate(tenant, review_id, "approve", "reviewer")
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _x: approve(), range(2)))
        service = InventoryReviewService(self.sessions)
        service.mutate(tenant, review_id, "correct", "reviewer", {"note": "checked"})
        with self.sessions() as session:
            review = session.get(InventoryReviewModel, review_id)
            events = list(session.scalars(select(InventoryReviewEventModel).where(InventoryReviewEventModel.tenant_id == tenant, InventoryReviewEventModel.review_id == review_id)))
            self.assertEqual(review.status, "approved")
            self.assertEqual([event.action for event in events], ["approve", "correct"])
            self.assertTrue(all(event.actor_id == "reviewer" and event.created_at for event in events))
            self.assertEqual(session.scalar(select(func.count(InventoryTransactionModel.id)).where(InventoryTransactionModel.tenant_id == tenant)), 0)

    def test_postgres_tenant_isolation_and_phase6_creative_isolation(self):
        tenant, other, ids, _raw = self._fixture(low_confidence=True)
        with self.sessions() as session:
            before = {model.__tablename__: session.scalar(select(func.count()).select_from(model)) for model in (SourceAssetModel, AssetModel, AssetPipelineModel, AssetAiAnalysisModel, SearchIndexRecordModel)}
        InventoryDocumentNormalizer(self.sessions).execute(self._normalize_job(tenant, ids["analysis"]))
        InventoryDocumentValidator(self.sessions).execute(self._validate_job(tenant, ids["doc"]))
        with self.sessions() as session:
            review_id = session.scalar(select(InventoryReviewModel.id).where(InventoryReviewModel.tenant_id == tenant))
        with self.assertRaises(ValueError):
            InventoryReviewService(self.sessions).mutate(tenant, review_id, "correct", "reviewer", {"item_id": ids["other_item"]})
        with self.assertRaises(LookupError):
            InventoryReviewService(self.sessions).mutate(other, review_id, "approve", "other")
        with self.sessions() as session:
            after = {model.__tablename__: session.scalar(select(func.count()).select_from(model)) for model in (SourceAssetModel, AssetModel, AssetPipelineModel, AssetAiAnalysisModel, SearchIndexRecordModel)}
            self.assertEqual(after, before)

    def test_inventory_pause_and_creative_pause_isolation_for_phase6_jobs(self):
        tenant, _other, ids, _raw = self._fixture()
        types = (INVENTORY_DOCUMENT_NORMALIZE_JOB, INVENTORY_DOCUMENT_VALIDATE_JOB)
        with self.sessions() as session:
            repo = InventoryJobRepository(session, types)
            priority = 1 + session.scalar(select(func.coalesce(func.max(InventoryJobModel.priority), 0)))
            normalize = repo.create_job(tenant_id=tenant, job_type=types[0], entity_type="inventory_ai_analysis", entity_id=ids["analysis"], idempotency_key=f"claim-normalize:{ids['analysis']}", priority=priority)
            validate = repo.create_job(tenant_id=tenant, job_type=types[1], entity_type="inventory_document", entity_id=ids["doc"], idempotency_key=f"claim-validate:{ids['doc']}", priority=priority)
            control = session.scalar(select(InventoryProcessingControlModel).where(InventoryProcessingControlModel.tenant_id == tenant)); control.paused = True; session.commit()
        with self.sessions() as session:
            claimed_while_paused = InventoryJobRepository(session, types).claim_next(worker_id="w", lease_seconds=30)
            self.assertNotIn(getattr(claimed_while_paused, "id", None), {normalize.id, validate.id})
            control = session.scalar(select(InventoryProcessingControlModel).where(InventoryProcessingControlModel.tenant_id == tenant)); control.paused = False
            session.add(TenantProcessingPolicyModel(tenant_id=tenant, pipeline_enabled=True, processing_paused=True)); session.commit()
            claimed = InventoryJobRepository(session, types).claim_next(worker_id="w", lease_seconds=30)
            self.assertIn(claimed.id, {normalize.id, validate.id})

@unittest.skipUnless(POSTGRES_AVAILABLE, "PostgreSQL integration database is not configured")
class InventoryPhase7PostgreSqlIntegrationTest(InventoryPhase6PostgreSqlIntegrationTest):
    """Phase 7 uses real PostgreSQL locking and immutable ledger constraints."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with cls.sessions() as session:
            session.execute(delete(InventoryJobModel).where(
                InventoryJobModel.job_type == INVENTORY_DOCUMENT_COMMIT_JOB,
            ))
            session.commit()

    def setUp(self):
        with self.sessions() as session:
            leaked = session.scalar(select(func.count(InventoryJobModel.id)).where(
                InventoryJobModel.job_type == INVENTORY_DOCUMENT_COMMIT_JOB,
                InventoryJobModel.status.in_(("queued", "leased", "running")),
            ))
            self.assertEqual(leaked, 0)

    def tearDown(self):
        with self.sessions() as session:
            session.execute(delete(InventoryJobModel).where(
                InventoryJobModel.job_type == INVENTORY_DOCUMENT_COMMIT_JOB,
            ))
            session.commit()
    def _approved_document(self, *, document_type="receipt", transfer=False):
        tenant, other, ids, raw = self._fixture()
        with self.sessions() as session:
            source = InventoryLocationModel(id=str(uuid4()), tenant_id=tenant, code=f"SRC-{ids['doc'][:8]}", name="Source")
            destination = InventoryLocationModel(id=str(uuid4()), tenant_id=tenant, code=f"DST-{ids['doc'][:8]}", name="Destination")
            session.add_all((source, destination))
            document = session.get(InventoryDocumentModel, ids["doc"])
            document.document_type = document_type
            document.location_id = source.id
            document.destination_location_id = destination.id if transfer else None
            document.business_date = date(2026, 8, 11)
            session.commit()
        InventoryDocumentNormalizer(self.sessions).execute(self._normalize_job(tenant, ids["analysis"]))
        InventoryDocumentValidator(self.sessions).execute(self._validate_job(tenant, ids["doc"]))
        return tenant, other, ids, raw

    @staticmethod
    def _commit_job(tenant, document_id):
        return InventoryJobModel(tenant_id=tenant, job_type=INVENTORY_DOCUMENT_COMMIT_JOB, entity_type="inventory_document", entity_id=document_id, idempotency_key=f"commit:{document_id}", payload_json={"document_id": document_id})

    def test_concurrent_commit_is_idempotent_and_creative_isolated(self):
        tenant, _other, ids, raw = self._approved_document()
        with self.sessions() as session:
            before = {model.__tablename__: session.scalar(select(func.count()).select_from(model)) for model in (SourceAssetModel, AssetModel, AssetPipelineModel, AssetAiAnalysisModel, SearchIndexRecordModel)}
            raw_before = session.get(InventoryAiAnalysisModel, ids["analysis"]).raw_result_json
        barrier = Barrier(2)
        def commit():
            barrier.wait(timeout=10)
            InventoryDocumentCommitter(self.sessions).execute(self._commit_job(tenant, ids["doc"]))
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _index: commit(), range(2)))
        with self.sessions() as session:
            rows = list(session.scalars(select(InventoryTransactionModel).where(InventoryTransactionModel.tenant_id == tenant)))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].transaction_type, "receipt")
            self.assertEqual(rows[0].quantity_base_unit, Decimal("2.5"))
            self.assertEqual(session.get(InventoryAiAnalysisModel, ids["analysis"]).raw_result_json, raw_before)
            after = {model.__tablename__: session.scalar(select(func.count()).select_from(model)) for model in (SourceAssetModel, AssetModel, AssetPipelineModel, AssetAiAnalysisModel, SearchIndexRecordModel)}
            self.assertEqual(after, before)

    def test_transfer_creates_two_atomic_tenant_scoped_legs(self):
        tenant, _other, ids, _raw = self._approved_document(document_type="warehouse_transfer", transfer=True)
        InventoryDocumentCommitter(self.sessions).execute(self._commit_job(tenant, ids["doc"]))
        with self.sessions() as session:
            document = session.get(InventoryDocumentModel, ids["doc"])
            rows = list(session.scalars(select(InventoryTransactionModel).where(InventoryTransactionModel.tenant_id == tenant, InventoryTransactionModel.source_document_id == document.id)))
            self.assertEqual({row.transaction_type for row in rows}, {"transfer_out", "transfer_in"})
            self.assertEqual({row.location_id for row in rows}, {document.location_id, document.destination_location_id})
            self.assertTrue(all(row.metadata_json["transfer_identity"] == document.id for row in rows))

    def test_commit_pause_and_cross_tenant_document_are_safe(self):
        tenant, other, ids, _raw = self._approved_document()
        with self.sessions() as session:
            control = session.scalar(select(InventoryProcessingControlModel).where(InventoryProcessingControlModel.tenant_id == tenant))
            control.paused = True
            job = InventoryJobRepository(session, (INVENTORY_DOCUMENT_COMMIT_JOB,)).create_job(tenant_id=tenant, job_type=INVENTORY_DOCUMENT_COMMIT_JOB, entity_type="inventory_document", entity_id=ids["doc"], idempotency_key=f"queue:{ids['doc']}", payload={"document_id": ids["doc"]})
            session.commit()
        with self.sessions() as session:
            repo = InventoryJobRepository(session, (INVENTORY_DOCUMENT_COMMIT_JOB,))
            self.assertIsNone(repo.claim_next(worker_id="phase7", lease_seconds=30))
            session.scalar(select(InventoryProcessingControlModel).where(InventoryProcessingControlModel.tenant_id == tenant)).paused = False
            session.commit()
        with self.sessions() as session:
            self.assertIsNotNone(InventoryJobRepository(session, (INVENTORY_DOCUMENT_COMMIT_JOB,)).claim_next(worker_id="phase7", lease_seconds=30))
        with self.assertRaises(InventoryBusinessFailure):
            InventoryDocumentCommitter(self.sessions).execute(self._commit_job(other, ids["doc"]))

@unittest.skipUnless(POSTGRES_AVAILABLE, "PostgreSQL integration database is not configured")
class InventoryPhase9PostgreSqlIntegrationTest(unittest.TestCase):
    """Real PostgreSQL concurrency coverage for daily scheduler/finalization."""

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        cls.sessions = sessionmaker(cls.engine, class_=Session, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def _tenant(self):
        marker = uuid4().hex
        tenant = f"inv-daily-{marker}"
        source = f"src-daily-{marker[:24]}"
        with self.sessions.begin() as session:
            session.add(TenantModel(id=tenant, name="Daily", slug=tenant))
            session.add(ExternalSourceModel(id=source, tenant_id=tenant, source_key=source, source_type="google_drive"))
            session.add(InventorySettingsModel(tenant_id=tenant, external_source_id=source, inbox_folder_id="inbox", enabled=True))
        return tenant

    def test_concurrent_check_and_force_finalize_are_idempotent(self):
        tenant = self._tenant()
        business_day = date(2030, 8, 9)
        barrier = Barrier(2)

        def check():
            barrier.wait(timeout=10)
            return InventoryDailyRunService(self.sessions).evaluate(tenant, business_day).id

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(1, len(set(executor.map(lambda _value: check(), range(2)))))

        barrier = Barrier(2)
        def finalize():
            barrier.wait(timeout=10)
            return InventoryDailyRunService(self.sessions).finalize(
                tenant, business_day, actor_id="daily-test", force=True, reason="test race"
            ).id

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(1, len(set(executor.map(lambda _value: finalize(), range(2)))))

        with self.sessions() as session:
            self.assertEqual(1, session.scalar(select(func.count(InventoryDailyRunModel.id)).where(InventoryDailyRunModel.tenant_id == tenant)))
            events = list(session.scalars(select(InventoryDailyRunEventModel).where(InventoryDailyRunEventModel.tenant_id == tenant).order_by(InventoryDailyRunEventModel.created_at)))
            self.assertEqual(["completeness_check", "forced_finalized"], [event.event_type for event in events])
            self.assertEqual("daily-test", events[-1].actor_id)
            self.assertTrue(events[-1].snapshot_json["blockers"])

    def test_scheduler_is_tenant_scoped_and_creative_isolated(self):
        tenant_a = self._tenant()
        tenant_b = self._tenant()
        business_day = date(2030, 8, 9)
        models = (SourceAssetModel, AssetModel, AssetPipelineModel, AssetAiAnalysisModel, SearchIndexRecordModel)
        with self.sessions() as session:
            before = {model.__tablename__: session.scalar(select(func.count()).select_from(model)) for model in models}
        InventoryDailyRunService(self.sessions).evaluate(tenant_a, business_day)
        self.assertIsNone(InventoryDailyRunService(self.sessions).get(tenant_b, business_day))
        InventoryDailyScheduler(self.sessions).run_once(datetime(2030, 8, 9, 9, 30, tzinfo=timezone.utc))
        with self.sessions() as session:
            after = {model.__tablename__: session.scalar(select(func.count()).select_from(model)) for model in models}
            self.assertEqual(before, after)
            self.assertEqual(1, session.scalar(select(func.count(InventoryDailyRunModel.id)).where(InventoryDailyRunModel.tenant_id == tenant_a)))
