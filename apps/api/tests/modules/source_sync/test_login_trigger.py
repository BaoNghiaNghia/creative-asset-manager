import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel, SourceAssetModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.source_sync.login_trigger import GoogleLoginSyncScheduler
from app.modules.source_sync.model import SourceSyncRunModel


class GoogleLoginSyncSchedulerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.settings = Settings(
            GOOGLE_AUTO_SCAN_ON_LOGIN_ENABLED=True,
            GOOGLE_FULL_SCAN_ON_FIRST_LOGIN_ENABLED=True,
        )
        self.scheduler = GoogleLoginSyncScheduler(self.session, self.settings)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    @staticmethod
    def cloud(account_id: str, connection_id: str):
        return SimpleNamespace(
            active_tenant_id="tenant-a",
            connection_id=connection_id,
            user={
                "id": account_id,
                "email": f"{account_id}@example.test",
                "name": account_id,
            },
        )

    def test_first_login_enqueues_reconciliation_with_stable_ids_only(self) -> None:
        result = self.scheduler.enqueue(self.cloud("google-a", "connection-a"))
        self.session.commit()

        self.assertIsNotNone(result)
        self.assertTrue(result.created)
        self.assertTrue(result.reconciliation)
        job = self.session.get(ProcessingJobModel, result.job_id)
        self.assertEqual(
            job.payload_json,
            {
                "external_source_id": result.external_source_id,
                "reconciliation": True,
            },
        )
        self.assertNotIn("oauth_connection_id", job.payload_json)
        self.assertNotIn("access_token", job.payload_json)
        self.assertNotIn("refresh_token", job.payload_json)

    def test_repeated_login_reuses_active_job(self) -> None:
        first = self.scheduler.enqueue(self.cloud("google-a", "connection-a"))
        second = self.scheduler.enqueue(self.cloud("google-a", "connection-a"))
        self.session.commit()

        self.assertEqual(first.job_id, second.job_id)
        self.assertFalse(second.created)
        count = self.session.scalar(
            select(func.count()).select_from(ProcessingJobModel)
        )
        self.assertEqual(count, 1)

    def test_login_after_completed_full_sync_enqueues_incremental(self) -> None:
        first = self.scheduler.enqueue(self.cloud("google-a", "connection-a"))
        job = self.session.get(ProcessingJobModel, first.job_id)
        job.status = "completed"
        self.session.add(SourceSyncRunModel(
            tenant_id="tenant-a",
            external_source_id=first.external_source_id,
            mode="full",
            generation=1,
            status="completed",
        ))
        self.session.commit()

        second = self.scheduler.enqueue(self.cloud("google-a", "connection-a"))
        self.session.commit()

        self.assertTrue(second.created)
        self.assertFalse(second.reconciliation)
        self.assertNotEqual(first.job_id, second.job_id)

    def test_reconnection_reuses_existing_source_for_same_google_account(self) -> None:
        first = self.scheduler.enqueue(self.cloud("google-a", "connection-old"))
        first_job = self.session.get(ProcessingJobModel, first.job_id)
        first_job.status = "completed"
        self.session.commit()

        second = self.scheduler.enqueue(self.cloud("google-a", "connection-new"))
        self.session.commit()

        self.assertEqual(second.external_source_id, first.external_source_id)
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(ExternalSourceModel)),
            1,
        )
        source = self.session.get(ExternalSourceModel, first.external_source_id)
        self.assertEqual(source.oauth_connection_id, "connection-new")
        self.assertEqual(source.source_metadata["provider_account_id"], "google-a")

    def test_reconnection_invalidates_existing_viewer_hierarchy_cache(self) -> None:
        first = self.scheduler.enqueue(self.cloud("google-a", "connection-old"))
        self.session.commit()

        with patch(
            "app.modules.source_sync.login_trigger.viewer_folder_hierarchy_cache.invalidate"
        ) as invalidate:
            second = self.scheduler.enqueue(self.cloud("google-a", "connection-new"))

        self.assertEqual(second.external_source_id, first.external_source_id)
        invalidate.assert_called_once_with(
            tenant_id="tenant-a", external_source_id=first.external_source_id
        )

    def test_reuses_populated_legacy_source_for_the_same_google_account(self) -> None:
        legacy = ExternalSourceModel(
            tenant_id="tenant-a",
            source_key="google-drive:legacy-connection",
            source_type="google_drive",
            source_metadata={
                "oauth_connection_id": "legacy-connection",
                "provider_account_id": "google-a",
            },
        )
        duplicate = ExternalSourceModel(
            tenant_id="tenant-a",
            source_key="google-drive:duplicate-connection",
            source_type="google_drive",
            source_metadata={
                "oauth_connection_id": "duplicate-connection",
                "provider_account_id": "google-a",
                "is_default": True,
            },
        )
        self.session.add_all([legacy, duplicate])
        self.session.flush()
        self.session.add(
            SourceAssetModel(
                tenant_id="tenant-a",
                external_source_id=legacy.id,
                external_asset_id="drive-file-1",
            )
        )
        self.session.commit()

        result = self.scheduler.enqueue(self.cloud("google-a", "connection-new"))
        self.session.commit()

        self.assertEqual(result.external_source_id, legacy.id)
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(ExternalSourceModel)),
            2,
        )
        self.assertTrue(
            self.session.get(ExternalSourceModel, legacy.id).source_metadata["is_default"]
        )
        self.assertFalse(
            self.session.get(ExternalSourceModel, duplicate.id).source_metadata["is_default"]
        )

    def test_different_google_accounts_use_different_sources(self) -> None:
        first = self.scheduler.enqueue(self.cloud("google-a", "connection-a"))
        second = self.scheduler.enqueue(self.cloud("google-b", "connection-b"))
        self.session.commit()

        self.assertNotEqual(first.external_source_id, second.external_source_id)
        sources = list(self.session.scalars(select(ExternalSourceModel)))
        self.assertEqual(
            {source.source_key for source in sources},
            {"google-drive:connection-a", "google-drive:connection-b"},
        )
        defaults = [source for source in sources if source.source_metadata.get("is_default")]
        self.assertEqual([source.source_key for source in defaults], ["google-drive:connection-b"])

    def test_disabled_flag_registers_default_source_without_enqueuing_job(self) -> None:
        disabled = GoogleLoginSyncScheduler(
            self.session,
            Settings(
                GOOGLE_AUTO_SCAN_ON_LOGIN_ENABLED=False,
                GOOGLE_FULL_SCAN_ON_FIRST_LOGIN_ENABLED=True,
            ),
        )
        self.assertIsNone(disabled.enqueue(self.cloud("google-a", "connection-a")))
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(ProcessingJobModel)
            ),
            0,
        )
        sources = list(self.session.scalars(select(ExternalSourceModel)))
        self.assertEqual(len(sources), 1)
        self.assertTrue(sources[0].source_metadata.get("is_default"))


if __name__ == "__main__":
    unittest.main()
