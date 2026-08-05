import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel, SourceSyncCursorModel
from app.modules.auth_persistence.model import OAuthConnectionModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.source_sync.scheduler import SourceSyncScheduler

class SourceSyncSchedulerTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.settings = Settings(PROCESSING_JOBS_ENABLED=True, INCREMENTAL_SOURCE_SYNC_ENABLED=True, SOURCE_SYNC_SCHEDULER_ENABLED=True, UNIFIED_ASSET_INGESTION_ENABLED=True)
        self.session = Session(self.engine, expire_on_commit=False)
        self.session.add(TenantProcessingPolicyModel(tenant_id="tenant-a", pipeline_enabled=True, source_sync_enabled=True))
        self.source = ExternalSourceModel(tenant_id="tenant-a", source_key="google-drive:source-a", source_type="google_drive", source_metadata={})
        self.session.add(self.source)
        self.session.flush()
        connection = OAuthConnectionModel(tenant_id="tenant-a", provider="google", provider_account_id="account-a", access_token_ciphertext="encrypted", key_version="v1", status="active")
        self.session.add(connection)
        self.session.flush()
        self.source.source_metadata = {"oauth_connection_id": connection.id}
        self.session.commit()
        self.factory = lambda: Session(self.engine, expire_on_commit=False)
        self.scheduler = SourceSyncScheduler(self.factory, self.settings)

    def tearDown(self):
        self.session.close(); self.engine.dispose()

    def test_disabled_scheduler_does_not_tick(self):
        disabled = SourceSyncScheduler(self.factory, Settings())
        self.assertEqual(disabled.tick(), ())

    def test_first_source_uses_full_scan(self):
        result = self.scheduler.tick()[0]
        self.assertEqual(result.mode, "full")
        self.assertTrue(result.created)
        self.assertTrue(self.session.get(ProcessingJobModel, result.job_id).payload_json["reconciliation"])

    def test_cursor_uses_incremental_scan(self):
        self.session.add(SourceSyncCursorModel(tenant_id="tenant-a", external_source_id=self.source.id, cursor_key="changes", cursor_value="cursor-1"))
        self.session.commit()
        result = self.scheduler.tick()[0]
        self.assertEqual(result.mode, "incremental")
        self.assertFalse(self.session.get(ProcessingJobModel, result.job_id).payload_json["reconciliation"])

    def test_active_job_prevents_duplicate(self):
        first = self.scheduler.tick()[0]
        second = self.scheduler.tick()[0]
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.job_id, second.job_id)

    def test_paused_tenant_is_skipped(self):
        policy = self.session.get(TenantProcessingPolicyModel, "tenant-a")
        policy.processing_paused = True
        self.session.commit()
        result = self.scheduler.tick()[0]
        self.assertEqual(result.skipped_reason, "tenant_policy_disabled_or_paused")

    def test_two_scheduler_instances_share_idempotency(self):
        other = SourceSyncScheduler(self.factory, self.settings)
        first, second = self.scheduler.tick()[0], other.tick()[0]
        self.assertEqual(first.job_id, second.job_id)
        self.assertEqual(self.session.query(ProcessingJobModel).count(), 1)

    def test_one_failing_source_does_not_stop_other_sources(self):
        second = ExternalSourceModel(tenant_id="tenant-a", source_key="google-drive:source-b", source_type="google_drive", source_metadata={"oauth_connection_id": self.source.source_metadata["oauth_connection_id"]})
        self.session.add(second); self.session.commit()
        original = self.scheduler.enqueue_source
        def enqueue(tenant_id, source_id, **kwargs):
            if source_id == self.source.id: raise RuntimeError("boom")
            return original(tenant_id, source_id, **kwargs)
        with patch.object(self.scheduler, "enqueue_source", side_effect=enqueue):
            results = self.scheduler.tick()
        self.assertEqual(len(results), 2)
        self.assertTrue(any(result.created for result in results))

if __name__ == "__main__":
    unittest.main()
