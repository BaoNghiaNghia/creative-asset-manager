import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.database import Base
from app.modules.application_logs.model import ApplicationLogModel
from app.modules.application_logs.repository import ApplicationLogRepository
from app.modules.auth_persistence.model import TenantModel
from app.modules.retention.service import RetentionCleanupService


class ApplicationLogRetentionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{self.tmp.name}/logs.db")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions() as session:
            session.add(TenantModel(id="tenant-a", name="Tenant A", slug="tenant-a"))
            session.flush()
            self.application, _ = ApplicationLogRepository(session).create_application(
                tenant_id="tenant-a", slug="worker", display_name="Worker", payload_schema=None
            )
            session.commit()

    def tearDown(self):
        self.engine.dispose(); self.tmp.cleanup()

    def test_background_retention_deletes_only_expired_logs(self):
        now = datetime.now(timezone.utc)
        with self.sessions() as session:
            application = session.merge(self.application)
            repository = ApplicationLogRepository(session)
            repository.create_log(application=application, idempotency_key=None, request_hash="a" * 64, level="info", event_type="expired", message=None, trace_id=None, payload={}, occurred_at=None, now=now - timedelta(days=11))
            repository.create_log(application=application, idempotency_key=None, request_hash="b" * 64, level="info", event_type="current", message=None, trace_id=None, payload={}, occurred_at=None, now=now - timedelta(days=9))
            session.commit()
        service = RetentionCleanupService(self.sessions, Settings(RETENTION_CLEANUP_BATCH_SIZE=10, RETENTION_CLEANUP_MAX_ROWS=10))
        run = service.create_run(tenant_id="tenant-a", record_types=("application_logs",), now=now)
        result = service.execute(tenant_id="tenant-a", run_id=run.id)
        self.assertEqual(result.status, "completed")
        with self.sessions() as session:
            rows = list(session.scalars(select(ApplicationLogModel)))
            self.assertEqual([row.event_type for row in rows], ["current"])
            self.assertEqual(session.scalar(select(func.count()).select_from(ApplicationLogModel)), 1)


if __name__ == "__main__": unittest.main()
