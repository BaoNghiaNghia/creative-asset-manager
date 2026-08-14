import tempfile
import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.database import Base
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.storage.managed_cleanup_scheduler import ManagedStorageCleanupScheduler


class ManagedStorageCleanupSchedulerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{self.tmp.name}/cleanup.db")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions() as session:
            session.add(TenantProcessingPolicyModel(tenant_id="tenant-enabled", pipeline_enabled=True))
            session.commit()

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def test_disabled_scheduler_creates_no_jobs(self):
        scheduler = ManagedStorageCleanupScheduler(
            self.sessions, Settings(PROCESSING_JOBS_ENABLED=True)
        )
        self.assertEqual(scheduler.schedule_known_tenants(), 0)
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(ProcessingJobModel)), 0)

    def test_enabled_scheduler_creates_bounded_tenant_job(self):
        scheduler = ManagedStorageCleanupScheduler(
            self.sessions, Settings(
                PROCESSING_JOBS_ENABLED=True,
                MANAGED_STORAGE_AUTO_CLEANUP_ENABLED=True,
                MANAGED_STORAGE_CLEANUP_INTERVAL_SECONDS=3600,
            )
        )
        self.assertEqual(scheduler.schedule_known_tenants(now=datetime.now(timezone.utc)), 1)
        with self.sessions() as session:
            job = session.scalar(select(ProcessingJobModel))
            self.assertEqual(job.tenant_id, "tenant-enabled")
            self.assertEqual(job.job_type, "managed_storage_cleanup")
