import tempfile
import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.database import Base
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.retention.model import RetentionCleanupRunModel
from app.modules.retention.scheduler import RetentionCleanupScheduler


class RetentionSchedulerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{self.tmp.name}/scheduler.db")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.settings = Settings(
            PROCESSING_JOBS_ENABLED=True,
            RETENTION_CLEANUP_ENABLED=True,
        )
        with self.sessions() as session:
            session.add(TenantProcessingPolicyModel(
                tenant_id="tenant-enabled", pipeline_enabled=True
            ))
            session.add(TenantProcessingPolicyModel(
                tenant_id="tenant-disabled", pipeline_enabled=False
            ))
            session.commit()

    def tearDown(self):
        self.engine.dispose(); self.tmp.cleanup()

    def test_existing_scheduler_creates_one_tenant_scoped_claimable_job(self):
        scheduler = RetentionCleanupScheduler(self.sessions, self.settings)
        now = datetime.now(timezone.utc)
        self.assertEqual(scheduler.schedule_known_tenants(now=now), 1)
        self.assertEqual(scheduler.schedule_known_tenants(now=now), 0)
        with self.sessions() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(RetentionCleanupRunModel)), 1
            )
            job = session.scalar(select(ProcessingJobModel))
            self.assertEqual(job.tenant_id, "tenant-enabled")
            self.assertEqual(job.payload_json, {"cleanup_run_id": job.entity_id})
            claimed = ProcessingRepository(session).claim_next_job(
                worker_id="cleanup-worker", lease_seconds=30, now=now,
                enforce_tenant_policy=True, allowed_job_types=("retention_cleanup",),
            )
            session.commit()
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed.id, job.id)
