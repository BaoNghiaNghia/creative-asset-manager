from __future__ import annotations

import os
import threading
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.assets.model import AssetModel
from app.modules.assets.repository import (
    AssetContentConflictError,
    AssetRegistryRepository,
)
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing.service import ProcessingJobService


DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL") or os.getenv("DATABASE_URL", "")
POSTGRES_AVAILABLE = DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg://"))


@unittest.skipUnless(POSTGRES_AVAILABLE, "PostgreSQL integration database is not configured")
class PostgreSqlRepositoryIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        cls.sessions = sessionmaker(cls.engine, class_=Session, expire_on_commit=False)
        if cls.engine.dialect.name != "postgresql":
            raise RuntimeError("PostgreSQL integration tests require PostgreSQL")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def test_migrations_reached_head_on_real_postgresql(self) -> None:
        tables = set(inspect(self.engine).get_table_names())
        self.assertIn("alembic_version", tables)
        self.assertIn("processing_jobs", tables)
        self.assertIn("oauth_connections", tables)
        with self.engine.connect() as connection:
            version = connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one()
        heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
        self.assertEqual(len(heads), 1)
        self.assertEqual(version, heads[0])

    def test_tenant_constraints_and_source_identity(self) -> None:
        marker = uuid4().hex
        content_hash = marker.ljust(64, "0")[:64]
        tenant_a = f"pg-a-{marker}"
        tenant_b = f"pg-b-{marker}"
        with self.sessions() as session:
            repository = AssetRegistryRepository(session)
            repository.create_asset(tenant_id=tenant_a, content_hash=content_hash)
            repository.create_asset(tenant_id=tenant_b, content_hash=content_hash)
            session.commit()

        with self.sessions() as session:
            with self.assertRaises(AssetContentConflictError):
                AssetRegistryRepository(session).create_asset(
                    tenant_id=tenant_a, content_hash=content_hash
                )
            session.rollback()

        with self.sessions() as session:
            repository = AssetRegistryRepository(session)
            first_source = repository.upsert_external_source(
                tenant_id=tenant_a,
                source_key=f"drive-{marker}",
                source_type="google_drive",
            )
            second_source = repository.upsert_external_source(
                tenant_id=tenant_a,
                source_key=f"sharepoint-{marker}",
                source_type="sharepoint",
            )
            first = repository.upsert_source_asset(
                tenant_id=tenant_a,
                external_source_id=first_source.id,
                external_asset_id="same-external-id",
            )
            second = repository.upsert_source_asset(
                tenant_id=tenant_a,
                external_source_id=second_source.id,
                external_asset_id="same-external-id",
            )
            session.commit()
            self.assertNotEqual(first.id, second.id)
            self.assertIsNone(repository.get_source_asset(tenant_b, first.id))
            self.assertEqual(
                session.scalar(
                    select(AssetModel).where(
                        AssetModel.tenant_id == tenant_a,
                        AssetModel.content_hash == content_hash,
                    )
                ).tenant_id,
                tenant_a,
            )

    def test_concurrent_workers_claim_one_job_with_skip_locked(self) -> None:
        marker = uuid4().hex
        with self.sessions() as session:
            job = ProcessingRepository(session).create_job(
                tenant_id=f"pg-claim-{marker}",
                job_type="source_asset_download",
                entity_type="source_asset",
                entity_id=marker,
                idempotency_key=f"pg-claim-{marker}",
            )
            session.commit()
            job_id = job.id

        barrier = threading.Barrier(2)
        claimed: list[str | None] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def worker(worker_id: str) -> None:
            try:
                barrier.wait(timeout=5)
                with self.sessions() as session:
                    result = ProcessingJobService(
                        ProcessingRepository(session)
                    ).claim_next(
                        worker_id=worker_id,
                        lease_seconds=30,
                        allowed_job_types=("source_asset_download",),
                    )
                with lock:
                    claimed.append(result.id if result else None)
            except BaseException as exc:
                with lock:
                    errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=("pg-worker-a",)),
            threading.Thread(target=worker, args=("pg-worker-b",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(errors)
        self.assertEqual(claimed.count(job_id), 1)
        self.assertEqual(claimed.count(None), 1)

    def test_expired_lease_is_recovered_without_cross_tenant_access(self) -> None:
        marker = uuid4().hex
        tenant_id = f"pg-lease-{marker}"
        with self.sessions() as session:
            ProcessingRepository(session).create_job(
                tenant_id=tenant_id,
                job_type="source_asset_download",
                entity_type="source_asset",
                entity_id=marker,
                idempotency_key=f"pg-lease-{marker}",
            )
            session.commit()
            now = datetime.now(timezone.utc)
        with self.sessions() as session:
            first = ProcessingJobService(ProcessingRepository(session)).claim_next(
                worker_id="dead-worker",
                lease_seconds=1,
                now=now,
                allowed_job_types=("source_asset_download",),
            )
        with self.sessions() as session:
            before_expiry = ProcessingJobService(
                ProcessingRepository(session)
            ).claim_next(
                worker_id="early-worker",
                lease_seconds=1,
                now=now + timedelta(milliseconds=500),
                allowed_job_types=("source_asset_download",),
            )
        with self.sessions() as session:
            recovered = ProcessingJobService(
                ProcessingRepository(session)
            ).claim_next(
                worker_id="recovery-worker",
                lease_seconds=30,
                now=now + timedelta(seconds=2),
                allowed_job_types=("source_asset_download",),
            )
        self.assertIsNotNone(first)
        self.assertIsNone(before_expiry)
        self.assertEqual(recovered.id, first.id)
        self.assertEqual(recovered.claimed_by, "recovery-worker")
        with self.sessions() as session:
            persisted = session.get(ProcessingJobModel, first.id)
            self.assertEqual(persisted.attempt_count, 2)


if __name__ == "__main__":
    unittest.main()
