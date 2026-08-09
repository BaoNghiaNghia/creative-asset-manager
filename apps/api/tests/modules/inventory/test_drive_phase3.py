from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import TenantModel
from app.modules.explorer.schema import AssetNode
from app.modules.inventory.drive.downloader import (
    InventoryDownloadFailure,
    InventoryFileDownloader,
)
from app.modules.inventory.drive.poller import InventoryDrivePoller
from app.modules.inventory.drive.storage import InventorySourceStorage
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.jobs.repository import InventoryJobRepository
from app.modules.inventory.model import InventoryProcessingControlModel
from app.modules.inventory.persistence_model import (
    InventorySettingsModel,
    InventorySourceFileModel,
)
from app.modules.inventory.repository import InventorySourceFileRepository
from app.modules.inventory.schema import InventorySourceFileInput


NOW = datetime(2030, 8, 9, 8, 0, tzinfo=timezone.utc)
TABLES = {
    "tenants", "external_sources", "inventory_processing_controls",
    "inventory_settings", "inventory_source_files", "inventory_jobs",
}


class FakeDrive:
    def __init__(self, pages, *, item=None):
        self.pages = pages
        self.item = item
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def children_page(self, parent_id, **kwargs):
        self.calls.append((parent_id, kwargs.get("page_token")))
        token = kwargs.get("page_token")
        return self.pages[token]

    async def get(self, _item_id):
        return self.item


class FakeResponse:
    def __init__(self, chunks):
        self.chunks = chunks

    async def aiter_bytes(self):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


async def no_close(_client, _response):
    return None


class InventoryDrivePhase3Test(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{Path(self.directory.name) / 'phase3.db'}"
        )
        event.listen(
            self.engine,
            "connect",
            lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"),
        )
        for table in Base.metadata.sorted_tables:
            if table.name in TABLES:
                table.create(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions() as session:
            session.add_all([
                TenantModel(id="tenant-a", name="A", slug="a"),
                TenantModel(id="tenant-b", name="B", slug="b"),
                ExternalSourceModel(
                    id="source-a", tenant_id="tenant-a", source_key="a",
                    source_type="google_drive",
                    source_metadata={"oauth_connection_id": "connection-a"},
                ),
                ExternalSourceModel(
                    id="source-b", tenant_id="tenant-b", source_key="b",
                    source_type="google_drive",
                    source_metadata={"oauth_connection_id": "connection-b"},
                ),
            ])
            session.commit()

    def tearDown(self):
        self.engine.dispose()
        self.directory.cleanup()

    def enable(self, session, tenant="tenant-a", source="source-a", *, paused=False):
        session.add_all([
            InventoryProcessingControlModel(
                tenant_id=tenant, enabled=True, paused=paused,
                max_active_jobs=1, max_ai_jobs=0,
            ),
            InventorySettingsModel(
                tenant_id=tenant, enabled=True, external_source_id=source,
                inbox_folder_id=f"inbox-{tenant}", drive_poll_interval_seconds=60,
            ),
        ])
        session.commit()

    @staticmethod
    def node(file_id, mime="image/jpeg", modified=NOW):
        return AssetNode(
            id=file_id, name=f"{file_id}.jpg", kind="image",
            mime_type=mime, modified_at=modified, size=4,
        )

    async def test_disabled_and_paused_poller_make_no_drive_request(self):
        def forbidden(_token):
            raise AssertionError("Drive client must not be constructed")

        with self.sessions() as session:
            self.enable(session)
            summary = await InventoryDrivePoller(
                session, automation_enabled=True, poller_enabled=False,
                client_factory=forbidden,
            ).poll_due()
            self.assertEqual(summary.files_listed, 0)
        with self.sessions() as session:
            control = session.scalar(select(InventoryProcessingControlModel))
            control.paused = True
            session.commit()
            summary = await InventoryDrivePoller(
                session, automation_enabled=True, poller_enabled=True,
                client_factory=forbidden,
            ).poll_due()
            self.assertEqual(summary.bindings, 0)

    async def test_poll_failure_does_not_persist_sensitive_exception_text(self):
        async def failing_token(_connection_id):
            raise RuntimeError("secret-token-must-not-be-persisted")

        with self.sessions() as session:
            self.enable(session)
            await InventoryDrivePoller(
                session,
                automation_enabled=True,
                poller_enabled=True,
                token_resolver=failing_token,
            ).poll_due()
            binding = session.scalar(select(InventorySettingsModel))
            self.assertEqual(binding.last_poll_error_code, "RuntimeError")
            self.assertNotIn(
                "secret-token",
                binding.last_poll_error_message or "",
            )

    async def test_pagination_mime_filter_folder_and_repeated_version(self):
        supported = self.node("jpeg")
        unsupported = self.node("heic", "image/heic")
        folder = AssetNode(
            id="folder", name="nested", kind="folder",
            mime_type="application/vnd.google-apps.folder",
        )
        newer = self.node("jpeg", modified=NOW + timedelta(minutes=1))
        drive = FakeDrive({None: ([supported, unsupported, folder], "next"), "next": ([], None)})
        tokens = []

        async def token(connection_id):
            tokens.append(connection_id)
            return "secret-token"

        with self.sessions() as session:
            self.enable(session)
            poller = InventoryDrivePoller(
                session, automation_enabled=True, poller_enabled=True,
                token_resolver=token, client_factory=lambda _access: drive,
            )
            first = await poller.poll_due()
            self.assertEqual(drive.calls, [("inbox-tenant-a", None), ("inbox-tenant-a", "next")])
            self.assertEqual(first.jobs_created, 1)
            self.assertEqual(first.unsupported, 1)
            self.assertEqual(first.folders_ignored, 1)
            self.assertEqual(tokens, ["connection-a"])
            self.assertEqual(
                session.scalar(select(func.count(InventorySourceFileModel.id))), 2
            )
            binding = session.scalar(select(InventorySettingsModel))
            binding.last_successful_poll_at = None
            session.commit()
            repeated = await poller.poll_due()
            self.assertEqual(repeated.jobs_created, 0)
            self.assertEqual(
                session.scalar(select(func.count(InventoryJobModel.id))), 1
            )

            drive.pages = {None: ([newer], None)}
            binding.last_successful_poll_at = None
            session.commit()
            changed = await poller.poll_due()
            self.assertEqual(changed.jobs_created, 1)
            self.assertEqual(
                session.scalar(select(func.count(InventorySourceFileModel.id))), 3
            )

    async def test_tenant_and_provider_binding_are_enforced(self):
        with self.sessions() as session:
            session.add_all([
                InventoryProcessingControlModel(
                    tenant_id="tenant-a", enabled=True, paused=False,
                    max_active_jobs=1, max_ai_jobs=0,
                ),
                InventorySettingsModel(
                    tenant_id="tenant-a", enabled=True,
                    external_source_id="source-b", inbox_folder_id="inbox",
                    drive_poll_interval_seconds=60,
                ),
            ])
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()

            source = session.get(ExternalSourceModel, "source-a")
            source.source_type = "sharepoint"
            self.enable(session)
            binding = session.scalar(select(InventorySettingsModel))
            poller = InventoryDrivePoller(
                session, automation_enabled=True, poller_enabled=True,
            )
            with self.assertRaises(ValueError):
                await poller.poll_binding(binding)

    def create_source(self, session, tenant, source, file_id):
        return InventorySourceFileRepository(session).register_with_result(
            tenant,
            InventorySourceFileInput(
                external_source_id=source, drive_file_id=file_id,
                filename=f"{file_id}.jpg", mime_type="image/jpeg",
                drive_modified_time=NOW, drive_size=4,
            ),
            status="queued",
        )[0]

    async def downloader(self, chunks, *, tenant="tenant-a", source="source-a", file_id="file", max_bytes=100):
        with self.sessions() as session:
            row = self.create_source(session, tenant, source, file_id)
            session.commit()
        provider = self.node(file_id)
        drive = FakeDrive({}, item=provider)

        async def token(_connection):
            return "access"

        async def opener(_token, _file_id, _range):
            return object(), FakeResponse(chunks)

        service = InventoryFileDownloader(
            self.sessions,
            storage=InventorySourceStorage(self.directory.name),
            max_bytes=max_bytes,
            token_resolver=token,
            client_factory=lambda _access: drive,
            stream_opener=opener,
            stream_closer=no_close,
        )
        job = InventoryJobModel(
            tenant_id=tenant, job_type="inventory_file_download",
            entity_type="inventory_source_file", entity_id=row.id,
            idempotency_key=f"download:{row.id}",
            payload_json={"source_file_id": row.id},
        )
        return service, job, row.id

    async def test_download_hash_namespace_and_content_duplicate(self):
        data = [b"ab", b"cd"]
        service, job, first_id = await self.downloader(data, file_id="first")
        await service.execute(job)
        service, job, second_id = await self.downloader(data, file_id="second")
        await service.execute(job)
        with self.sessions() as session:
            first = session.get(InventorySourceFileModel, first_id)
            second = session.get(InventorySourceFileModel, second_id)
            self.assertEqual(first.content_sha256, hashlib.sha256(b"abcd").hexdigest())
            self.assertEqual(first.status, "downloaded")
            self.assertTrue(first.storage_key.startswith("inventory/tenant-a/source/"))
            self.assertEqual(second.status, "duplicate")
            self.assertEqual(second.duplicate_of_source_file_id, first.id)
            self.assertEqual(second.storage_key, first.storage_key)

    async def test_equal_hashes_do_not_deduplicate_across_tenants(self):
        service, job, first_id = await self.downloader([b"same"], file_id="a")
        await service.execute(job)
        service, job, second_id = await self.downloader(
            [b"same"], tenant="tenant-b", source="source-b", file_id="b"
        )
        await service.execute(job)
        with self.sessions() as session:
            first = session.get(InventorySourceFileModel, first_id)
            second = session.get(InventorySourceFileModel, second_id)
            self.assertEqual(second.status, "downloaded")
            self.assertNotEqual(first.storage_key, second.storage_key)

    async def test_partial_and_terminal_failures_leave_no_partial_blob(self):
        service, job, source_id = await self.downloader(
            [b"ok", OSError("network")], file_id="partial"
        )
        with self.assertRaises(InventoryDownloadFailure) as caught:
            await service.execute(job)
        self.assertTrue(caught.exception.retryable)
        with self.sessions() as session:
            self.assertEqual(
                session.get(InventorySourceFileModel, source_id).status,
                "retryable_failure",
            )
        self.assertEqual(list(Path(self.directory.name).rglob("*.partial")), [])

        service, job, source_id = await self.downloader(
            [b"too-large"], file_id="large", max_bytes=3
        )
        with self.assertRaises(InventoryDownloadFailure) as caught:
            await service.execute(job)
        self.assertFalse(caught.exception.retryable)
        with self.sessions() as session:
            self.assertEqual(
                session.get(InventorySourceFileModel, source_id).status,
                "terminal_failure",
            )
        self.assertEqual(list(Path(self.directory.name).rglob("*.partial")), [])
    def test_retryable_job_becomes_terminal_at_attempt_limit(self):
        with self.sessions() as session:
            session.add(
                InventoryProcessingControlModel(
                    tenant_id="tenant-a", enabled=True, paused=False,
                    max_active_jobs=1, max_ai_jobs=0,
                )
            )
            repository = InventoryJobRepository(
                session, ("inventory_file_download",)
            )
            job = repository.create_job(
                tenant_id="tenant-a",
                job_type="inventory_file_download",
                entity_type="inventory_source_file",
                entity_id="missing",
                idempotency_key="attempt-limit",
                max_attempts=1,
            )
            session.flush()
            claimed = repository.claim_next(
                worker_id="worker", lease_seconds=60, now=NOW
            )
            self.assertEqual(claimed.id, job.id)
            self.assertFalse(
                repository.fail(
                    claimed,
                    "worker",
                    error_code="transient",
                    error_message="transient",
                    retryable=True,
                    now=NOW,
                )
            )
            self.assertEqual(job.status, "failed")
