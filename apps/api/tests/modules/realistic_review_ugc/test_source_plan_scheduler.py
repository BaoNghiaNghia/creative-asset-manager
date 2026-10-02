import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.modules.auth_persistence.model import OAuthConnectionModel
from app.modules.realistic_review_ugc.source_plan_scheduler import (
    RrugcSourcePlanSyncScheduler,
)
from app.modules.realistic_review_ugc.source_plans import (
    RRUGC_SOURCE_ROOT_FOLDER_ID,
    SourcePlanSyncResult,
)
from app.modules.storage.managed_oauth import MANAGED_STORAGE_OAUTH_PROVIDER


class RrugcSourcePlanSyncSchedulerTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = lambda: Session(self.engine, expire_on_commit=False)
        self.settings = Settings(
            PROCESSING_JOBS_ENABLED=True,
            MANAGED_ASSET_STORAGE_ENABLED=True,
            RRUGC_SOURCE_AUTO_SYNC_ENABLED=True,
            RRUGC_SOURCE_AUTO_SYNC_INTERVAL_SECONDS=120,
            GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID="managed-root",
        )
        with self.factory() as session:
            session.add_all(
                [
                    OAuthConnectionModel(
                        id="managed-a",
                        tenant_id="tenant-a",
                        provider=MANAGED_STORAGE_OAUTH_PROVIDER,
                        provider_account_id="drive-a",
                        connection_purpose="managed_storage",
                        key_version="v1",
                        status="active",
                        provider_metadata_json={"root_folder_id": "managed-root"},
                    ),
                    OAuthConnectionModel(
                        id="managed-other-root",
                        tenant_id="tenant-b",
                        provider=MANAGED_STORAGE_OAUTH_PROVIDER,
                        provider_account_id="drive-b",
                        connection_purpose="managed_storage",
                        key_version="v1",
                        status="active",
                        provider_metadata_json={"root_folder_id": "different-root"},
                    ),
                ]
            )
            session.commit()

    def tearDown(self):
        self.engine.dispose()

    def test_tick_scans_matching_managed_storage_tenant(self):
        calls = []

        async def syncer(session, **kwargs):
            calls.append(kwargs)
            return SourcePlanSyncResult(
                root_folder_id=kwargs["root_folder_id"],
                folders_scanned=3,
                images_found=7,
                plans_created=1,
                plans_updated=0,
                plans_missing=0,
                jobs_queued=1,
                unchanged=6,
            )

        scheduler = RrugcSourcePlanSyncScheduler(
            self.factory,
            self.settings,
            syncer=syncer,
        )

        results = scheduler.tick()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].images_found, 7)
        self.assertEqual(calls[0]["tenant_id"], "tenant-a")
        self.assertEqual(calls[0]["user_id"], "system:rrugc-source-auto-sync")
        self.assertEqual(calls[0]["root_folder_id"], RRUGC_SOURCE_ROOT_FOLDER_ID)
        self.assertEqual(scheduler.interval_seconds, 120)

    def test_disabled_scheduler_does_not_scan(self):
        settings = self.settings.model_copy(
            update={"RRUGC_SOURCE_AUTO_SYNC_ENABLED": False}
        )
        scheduler = RrugcSourcePlanSyncScheduler(self.factory, settings)

        self.assertFalse(scheduler.enabled)
        self.assertEqual(scheduler.tick(), ())


if __name__ == "__main__":
    unittest.main()
