import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel, SourceAssetModel, SourceSyncCursorModel
from app.modules.auth_persistence.model import OAuthConnectionModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.source_sync.model import SourceSyncRunModel
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

    def _decommissioned_source(self, *, oauth_connection_id: str = "stale-connection") -> ExternalSourceModel:
        source = ExternalSourceModel(
            tenant_id="tenant-a",
            source_key=f"google-drive:legacy-{oauth_connection_id}",
            source_type="google_drive",
            source_metadata={
                "oauth_connection_id": oauth_connection_id,
                "canonical_source_id": self.source.id,
                "decommissioned_at": "2026-08-15T00:00:00+00:00",
                "decommissioned_reason": "duplicate_google_drive_source",
                "is_default": False,
            },
        )
        self.session.add(source)
        self.session.commit()
        return source

    def test_decommissioned_source_is_skipped_before_credential_resolution(self):
        legacy = self._decommissioned_source()
        before = self.session.query(ProcessingJobModel).count()

        with patch.object(self.scheduler, "_source_credentials") as credentials:
            result = self.scheduler.enqueue_source("tenant-a", legacy.id)

        credentials.assert_not_called()
        self.assertFalse(result.created)
        self.assertIsNone(result.job_id)
        self.assertIsNone(result.mode)
        self.assertEqual(result.skipped_reason, "source_decommissioned")
        self.assertEqual(self.session.query(ProcessingJobModel).count(), before)

    def test_decommissioned_source_with_valid_credentials_is_not_scheduled(self):
        legacy = self._decommissioned_source(
            oauth_connection_id=self.source.source_metadata["oauth_connection_id"]
        )

        result = self.scheduler.enqueue_source("tenant-a", legacy.id)

        self.assertFalse(result.created)
        self.assertEqual(result.skipped_reason, "source_decommissioned")
        self.assertEqual(self.session.query(ProcessingJobModel).count(), 0)

    def test_canonical_source_id_without_decommissioned_at_remains_schedulable(self):
        source = ExternalSourceModel(
            tenant_id="tenant-a",
            source_key="google-drive:canonical-reference-only",
            source_type="google_drive",
            source_metadata={
                "oauth_connection_id": self.source.source_metadata["oauth_connection_id"],
                "canonical_source_id": self.source.id,
                "decommissioned_at": "   ",
            },
        )
        self.session.add(source)
        self.session.commit()

        result = self.scheduler.enqueue_source("tenant-a", source.id)

        self.assertTrue(result.created)
        self.assertEqual(result.mode, "full")

    def test_non_default_and_empty_decommissioned_at_remain_schedulable(self):
        for suffix, metadata in (
            ("non-default", {
                "oauth_connection_id": self.source.source_metadata["oauth_connection_id"],
                "is_default": False,
            }),
            ("empty-marker", {
                "oauth_connection_id": self.source.source_metadata["oauth_connection_id"],
                "decommissioned_at": "",
            }),
        ):
            with self.subTest(suffix=suffix):
                source = ExternalSourceModel(
                    tenant_id="tenant-a",
                    source_key=f"google-drive:{suffix}",
                    source_type="google_drive",
                    source_metadata=metadata,
                )
                self.session.add(source)
                self.session.commit()

                result = self.scheduler.enqueue_source("tenant-a", source.id)

                self.assertTrue(result.created)
                self.assertEqual(result.mode, "full")

    def test_tick_filters_decommissioned_source_and_preserves_history(self):
        legacy = self._decommissioned_source()
        asset = SourceAssetModel(
            tenant_id="tenant-a",
            external_source_id=legacy.id,
            external_asset_id="historical-file",
            filename="historical.png",
            deleted_at=self.source.created_at,
        )
        cursor = SourceSyncCursorModel(
            tenant_id="tenant-a",
            external_source_id=legacy.id,
            cursor_key="changes",
            cursor_value="historical-cursor",
        )
        run = SourceSyncRunModel(
            tenant_id="tenant-a",
            external_source_id=legacy.id,
            mode="full",
            generation=1,
            status="completed",
        )
        self.session.add_all([asset, cursor, run])
        self.session.commit()
        history = (
            self.session.query(SourceAssetModel).filter_by(external_source_id=legacy.id).count(),
            self.session.query(SourceSyncCursorModel).filter_by(external_source_id=legacy.id).count(),
            self.session.query(SourceSyncRunModel).filter_by(external_source_id=legacy.id).count(),
        )

        results = self.scheduler.tick()

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].created)
        self.assertEqual(
            self.session.query(ProcessingJobModel)
            .filter_by(entity_id=legacy.id, job_type="source_sync")
            .count(),
            0,
        )
        self.assertEqual(
            (
                self.session.query(SourceAssetModel).filter_by(external_source_id=legacy.id).count(),
                self.session.query(SourceSyncCursorModel).filter_by(external_source_id=legacy.id).count(),
                self.session.query(SourceSyncRunModel).filter_by(external_source_id=legacy.id).count(),
            ),
            history,
        )

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
