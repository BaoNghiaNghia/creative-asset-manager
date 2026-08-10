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
from threading import Barrier
from uuid import uuid4

from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.assets.model import AssetModel, ExternalSourceModel, SourceAssetModel
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.auth_persistence.model import TenantModel
from app.modules.inventory.persistence_model import InventoryDocumentModel, InventoryDocumentPageModel
from app.modules.inventory.jobs.repository import InventoryJobRepository
from app.modules.explorer.schema import AssetNode
from app.modules.inventory.drive.downloader import InventoryFileDownloader
from app.modules.inventory.drive.poller import InventoryDrivePoller
from app.modules.inventory.drive.storage import InventorySourceStorage
from app.modules.inventory.preparation.image import InventoryImagePreparationLimits, StatelessInventoryImagePreparer
from app.modules.inventory.preparation.service import INVENTORY_DOCUMENT_PREPARE_JOB, InventoryDocumentPreparer
from app.modules.inventory.preparation.storage import InventoryPreparedStorage
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.model import InventoryProcessingControlModel
from app.modules.inventory.persistence_model import InventorySettingsModel, InventorySourceFileModel
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
