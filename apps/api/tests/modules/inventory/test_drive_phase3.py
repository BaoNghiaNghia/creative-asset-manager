from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.auth_persistence.encryption import TokenCipher
from app.modules.auth_persistence.repository import AuthPersistenceRepository
from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import TenantModel
from app.modules.explorer.schema import AssetNode
from app.modules.inventory.drive.downloader import (
    InventoryDownloadFailure,
    InventoryFileDownloader,
)
from app.modules.inventory.drive.poller import InventoryDrivePoller
from app.modules.inventory.drive.storage import InventorySourceStorage, InventoryStorageError
from app.modules.inventory.jobs.registry import InventoryHandlerRegistry
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.jobs.repository import InventoryJobRepository
from app.modules.inventory.model import InventoryProcessingControlModel
from app.modules.inventory.persistence_model import (
    InventorySettingsModel,
    InventorySourceFileModel,
)
from app.modules.inventory.repository import InventorySourceFileRepository
from app.modules.inventory.schema import InventorySourceFileInput
from app.modules.inventory.worker import run_inventory_worker


NOW = datetime(2030, 8, 9, 8, 0, tzinfo=timezone.utc)
TABLES = {
    "tenants", "external_sources", "inventory_processing_controls",
    "inventory_settings", "inventory_source_files", "inventory_jobs",
    "oauth_connections", "auth_audit_events",
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
        secrets = (
            "access-token-DO-NOT-LOG",
            "refresh-token-DO-NOT-LOG",
            "authorization-code-DO-NOT-LOG",
            "credential-string-DO-NOT-LOG",
        )
        async def failing_token(_connection_id):
            raise RuntimeError(" ".join(secrets))

        with self.sessions() as session:
            self.enable(session)
            with self.assertLogs("cam.inventory.drive", level="WARNING") as captured:
                await InventoryDrivePoller(
                    session,
                    automation_enabled=True,
                    poller_enabled=True,
                    token_resolver=failing_token,
                ).poll_due()
            binding = session.scalar(select(InventorySettingsModel))
            self.assertEqual(binding.last_poll_error_code, "RuntimeError")
            rendered = "\n".join(captured.output)
            persisted = binding.last_poll_error_message or ""
            for secret in secrets:
                self.assertNotIn(secret, rendered)
                self.assertNotIn(secret, persisted)
            self.assertIn("error_code=RuntimeError", rendered)

    async def test_pagination_mime_filter_folder_and_repeated_version(self):
        supported = self.node("jpeg")
        unsupported = self.node("pdf", "application/pdf")
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
                max_attempts=2,
            )
            session.flush()
            claimed = repository.claim_next(
                worker_id="worker", lease_seconds=60, now=NOW
            )
            self.assertEqual(claimed.id, job.id)
            self.assertTrue(
                repository.fail(
                    claimed,
                    "worker",
                    error_code="transient",
                    error_message="transient",
                    retryable=True,
                    now=NOW,
                )
            )
            self.assertEqual(job.status, "retry")
            self.assertEqual(
                job.next_attempt_at,
                NOW + timedelta(seconds=1),
            )
            claimed = repository.claim_next(
                worker_id="worker",
                lease_seconds=60,
                now=NOW + timedelta(seconds=2),
            )
            self.assertEqual(claimed.id, job.id)
            self.assertFalse(
                repository.fail(
                    claimed,
                    "worker",
                    error_code="transient",
                    error_message="transient",
                    retryable=True,
                    now=NOW + timedelta(seconds=2),
                )
            )
            self.assertEqual(job.status, "failed")
    async def test_missing_inbox_binding_is_clean_noop(self):
        def forbidden(_token):
            raise AssertionError("Drive client must not be constructed")

        with self.sessions() as session:
            summary = await InventoryDrivePoller(
                session,
                automation_enabled=True,
                poller_enabled=True,
                client_factory=forbidden,
            ).poll_due()
            self.assertEqual(summary.bindings, 0)
            self.assertEqual(summary.files_listed, 0)
            self.assertEqual(
                session.scalar(select(func.count(InventorySourceFileModel.id))), 0
            )
            self.assertEqual(
                session.scalar(select(func.count(InventoryJobModel.id))), 0
            )

    async def test_disabled_inbox_binding_is_explicit_stable_noop(self):
        def forbidden(_token):
            raise AssertionError("Drive client must not be constructed")

        with self.sessions() as session:
            self.enable(session)
            binding = session.scalar(select(InventorySettingsModel))
            binding.enabled = False
            session.commit()
            poller = InventoryDrivePoller(
                session,
                automation_enabled=True,
                poller_enabled=True,
                client_factory=forbidden,
            )
            for _attempt in range(2):
                summary = await poller.poll_due()
                self.assertEqual(summary.bindings, 0)
                self.assertEqual(summary.files_listed, 0)
            session.refresh(binding)
            self.assertFalse(binding.enabled)
            self.assertIsNone(binding.last_successful_poll_at)
            self.assertEqual(
                session.scalar(select(func.count(InventorySourceFileModel.id))), 0
            )
            self.assertEqual(
                session.scalar(select(func.count(InventoryJobModel.id))), 0
            )

    async def test_all_supported_mimes_enqueue_and_unsupported_are_terminal(self):
        supported = (
            ("jpeg", "image/jpeg"),
            ("png", "image/png"),
            ("webp", "image/webp"),
            ("avif", "image/avif"),
            ("heic", "image/heic"),
            ("heif", "image/heif"),
        )
        unsupported = (
            ("pdf", "application/pdf"),
        )
        nodes = [
            self.node(file_id, mime)
            for file_id, mime in (*supported, *unsupported)
        ]
        drive = FakeDrive({None: (nodes, None)})
        with self.sessions() as session:
            self.enable(session)
            summary = await InventoryDrivePoller(
                session,
                automation_enabled=True,
                poller_enabled=True,
                token_resolver=lambda _connection: asyncio.sleep(
                    0, result="access"
                ),
                client_factory=lambda _access: drive,
            ).poll_due()
            rows = {
                row.drive_file_id: row
                for row in session.scalars(select(InventorySourceFileModel))
            }
            self.assertEqual(summary.jobs_created, len(supported))
            self.assertEqual(summary.unsupported, len(unsupported))
            self.assertEqual(
                session.scalar(select(func.count(InventoryJobModel.id))),
                len(supported),
            )
            for file_id, _mime in supported:
                self.assertEqual(rows[file_id].status, "queued")
            for file_id, _mime in unsupported:
                self.assertEqual(rows[file_id].status, "unsupported")
                self.assertEqual(
                    rows[file_id].last_error_code,
                    "unsupported_inventory_mime_type",
                )

        service = InventoryFileDownloader(
            self.sessions,
            storage=InventorySourceStorage(self.directory.name),
            max_bytes=100,
        )
        for file_id, _mime in unsupported:
            row = rows[file_id]
            job = InventoryJobModel(
                tenant_id=row.tenant_id,
                job_type="inventory_file_download",
                entity_type="inventory_source_file",
                entity_id=row.id,
                idempotency_key=f"unsupported:{row.id}",
                payload_json={"source_file_id": row.id},
            )
            with self.assertRaises(InventoryDownloadFailure) as raised:
                await service.execute(job)
            self.assertFalse(raised.exception.retryable)

    async def test_expired_persisted_oauth_refresh_is_used_without_secret_exposure(self):
        expired_access = "expired-access-token-DO-NOT-LOG"
        refresh_token = "refresh-token-DO-NOT-LOG"
        refreshed_access = "refreshed-access-token-DO-NOT-LOG"
        client_secret = "oauth-client-secret-DO-NOT-LOG"
        cipher = TokenCipher({"v1": b"1" * 32}, "v1")

        with self.sessions() as session:
            repository = AuthPersistenceRepository(session, cipher)
            connection = repository.upsert_connection(
                tenant_id="tenant-a",
                provider="google",
                provider_account_id="inventory-account",
                account_email="inventory@example.test",
                access_token=expired_access,
                refresh_token=refresh_token,
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
                scopes=["https://www.googleapis.com/auth/drive"],
                token_type="Bearer",
            )
            source = session.get(ExternalSourceModel, "source-a")
            source.source_metadata = {"oauth_connection_id": connection.id}
            session.commit()
            connection_id = connection.id
            source_row = self.create_source(
                session, "tenant-a", "source-a", "oauth-refresh-file"
            )
            session.commit()

        @contextmanager
        def persisted_auth_repository():
            with self.sessions() as session:
                repository = AuthPersistenceRepository(session, cipher)
                try:
                    yield repository
                    session.commit()
                except Exception:
                    session.rollback()
                    raise

        credentials = SimpleNamespace(
            token=refreshed_access,
            refresh_token=refresh_token,
            expiry=datetime.now(timezone.utc) + timedelta(hours=1),
            granted_scopes=("https://www.googleapis.com/auth/drive",),
            scopes=("https://www.googleapis.com/auth/drive",),
            refresh=lambda _request: None,
        )
        used_tokens = []
        drive = FakeDrive({}, item=self.node("oauth-refresh-file"))

        def client_factory(token):
            used_tokens.append(token)
            return drive

        async def opener(token, _file_id, _range):
            used_tokens.append(token)
            return object(), FakeResponse([b"oauth-refresh-content"])

        async def run_refresh(function, *args):
            return function(*args)

        service = InventoryFileDownloader(
            self.sessions,
            storage=InventorySourceStorage(self.directory.name),
            max_bytes=100,
            client_factory=client_factory,
            stream_opener=opener,
            stream_closer=no_close,
        )
        job = InventoryJobModel(
            tenant_id="tenant-a",
            job_type="inventory_file_download",
            entity_type="inventory_source_file",

            entity_id=source_row.id,
            idempotency_key=f"oauth:{source_row.id}",
            payload_json={"source_file_id": source_row.id},
        )
        with (
            patch(
                "app.providers.google.auth.auth_repository",
                persisted_auth_repository,
            ),
            patch(
                "app.providers.google.auth.get_settings",
                return_value=SimpleNamespace(AUTH_REFRESH_LEASE_SECONDS=60),
            ),
            patch(
                "app.providers.google.auth._settings",
                return_value=("client-id", client_secret, "https://callback"),
            ),
            patch(
                "app.providers.google.auth.Credentials",
                return_value=credentials,
            ),
            patch(
                "app.providers.google.auth.run_in_threadpool",
                side_effect=run_refresh,
            ),
            self.assertLogs("cam.inventory.drive", level="INFO") as captured,
        ):
            result = await service.execute(job)

        self.assertIsNone(result)
        self.assertEqual(used_tokens, [refreshed_access, refreshed_access])
        rendered_logs = "\n".join(captured.output)
        for secret in (
            expired_access,
            refresh_token,
            refreshed_access,
            client_secret,
        ):
            self.assertNotIn(secret, rendered_logs)
        with persisted_auth_repository() as repository:
            refreshed = repository.load_connection(
                provider="google", connection_id=connection_id
            )
            self.assertEqual(refreshed.access_token, refreshed_access)

    def test_retryable_storage_finalization_is_cleaned_and_worker_marks_terminal(self):
        class FailingPending:
            def __init__(self, pending):
                self.pending = pending
                self.sha256 = pending.sha256
                self.size_bytes = pending.size_bytes

            def commit(self, _suffix):
                raise InventoryStorageError(
                    "inventory_storage_temporarily_unavailable"
                )

            def discard(self):
                self.pending.discard()

        class FailingStorage(InventorySourceStorage):
            async def prepare(self, **kwargs):
                pending = await super().prepare(**kwargs)
                return FailingPending(pending)

        with self.sessions() as session:
            session.add(
                InventoryProcessingControlModel(
                    tenant_id="tenant-a",
                    enabled=True,
                    paused=False,
                    max_active_jobs=1,
                    max_ai_jobs=0,
                )
            )
            source = self.create_source(
                session, "tenant-a", "source-a", "storage-retry"
            )
            job = InventoryJobRepository(
                session, ("inventory_file_download",)
            ).create_job(
                tenant_id="tenant-a",
                job_type="inventory_file_download",
                entity_type="inventory_source_file",
                entity_id=source.id,
                idempotency_key=f"storage-retry:{source.id}",
                payload={"source_file_id": source.id},
                max_attempts=1,
            )
            session.commit()
            source_id, job_id = source.id, job.id

        drive = FakeDrive({}, item=self.node("storage-retry"))

        async def token(_connection):
            return "safe-access"

        async def opener(_token, _file_id, _range):
            return object(), FakeResponse([b"partial-content"])

        service = InventoryFileDownloader(
            self.sessions,
            storage=FailingStorage(self.directory.name),
            max_bytes=100,
            token_resolver=token,
            client_factory=lambda _access: drive,
            stream_opener=opener,
            stream_closer=no_close,
        )
        registry = InventoryHandlerRegistry()
        registry.register(
            "inventory_file_download",
            lambda claimed: asyncio.run(service.execute(claimed)),
        )

        class OneIterationEvent:
            def __init__(self):
                self.calls = 0

            def wait(self, _timeout):
                self.calls += 1
                return self.calls > 1

            def set(self):
                self.calls = 2

        class NoopHealthServer:
            def start(self):
                return None

            def close(self):
                return None

        config = SimpleNamespace(
            worker_id="inventory-storage-test",
            enabled=True,
            drive_poller_enabled=False,
            idle_poll_seconds=0,
            health_host="127.0.0.1",
            health_port=0,
            lease_seconds=60,
        )
        with (
            patch(
                "app.modules.inventory.worker.InventoryWorkerConfig.from_settings",
                return_value=config,
            ),
            patch(
                "app.modules.inventory.worker.InventoryWorkerHealthServer",
                return_value=NoopHealthServer(),
            ),
            patch(
                "app.modules.inventory.worker.build_inventory_handler_registry",
                return_value=registry,
            ),
            patch(
                "app.modules.inventory.worker.SessionLocal",
                self.sessions,
            ),
            patch(
                "app.modules.inventory.worker.threading.Event",
                return_value=OneIterationEvent(),
            ),
            patch("app.modules.inventory.worker.signal.signal"),
        ):
            self.assertEqual(run_inventory_worker(SimpleNamespace()), 0)

        with self.sessions() as session:
            source = session.get(InventorySourceFileModel, source_id)
            job = session.get(InventoryJobModel, job_id)
            self.assertEqual(source.status, "terminal_failure")
            self.assertEqual(
                source.last_error_code,
                "inventory_storage_temporarily_unavailable",
            )
            self.assertEqual(job.status, "failed")
            self.assertEqual(job.attempt_count, 1)
        self.assertEqual(list(Path(self.directory.name).rglob("*.partial")), [])
        completed = [
            path
            for path in Path(self.directory.name).rglob("*")
            if path.is_file() and path.suffix != ".db"
        ]
        self.assertEqual(completed, [])
