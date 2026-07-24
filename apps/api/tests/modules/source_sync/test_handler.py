import logging
import unittest
from threading import Event

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.domain.processing.handlers import (
    ClaimedJob,
    JobHandlerContext,
    JobOutcome,
    WorkerDependencies,
)
from app.domain.providers.contracts import SourceChangePage
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.source_sync.handler import SourceSyncJobHandler


class FakeGoogleProvider:
    def __init__(self):
        self.inputs = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def list_changes(self, input):
        self.inputs.append(input)
        return SourceChangePage((), "cursor", False)


class SourceSyncJobHandlerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        with Session(self.engine, expire_on_commit=False) as session:
            self.source = AssetRegistryRepository(session).upsert_external_source(
                tenant_id="tenant-a",
                source_key="google-drive:connection-a",
                source_type="google_drive",
                source_metadata={
                    "oauth_connection_id": "connection-a",
                    "provider_account_id": "google-a",
                },
            )
            session.commit()
            session.expunge(self.source)
        self.settings = Settings(
            GOOGLE_AUTO_SCAN_ON_LOGIN_ENABLED=True,
            GOOGLE_FULL_SCAN_ON_FIRST_LOGIN_ENABLED=True,
            PROCESSING_JOBS_ENABLED=True,
            UNIFIED_ASSET_INGESTION_ENABLED=True,
            INCREMENTAL_SOURCE_SYNC_ENABLED=True,
        )

    def tearDown(self) -> None:
        self.engine.dispose()

    def context(self, provider: FakeGoogleProvider) -> JobHandlerContext:
        async def resolve_token(connection_id: str) -> str:
            self.assertEqual(connection_id, "connection-a")
            return "access-token"

        dependencies = WorkerDependencies(
            session_factory=lambda: Session(self.engine, expire_on_commit=False),
            source_provider_factory=lambda provider_name, token: self._provider(
                provider, provider_name, token
            ),
            resources={
                "google_connection_access_token_resolver": resolve_token,
            },
        )
        return JobHandlerContext(
            job=ClaimedJob(
                id="sync-job",
                tenant_id="tenant-a",
                job_type="source_sync",
                entity_type="external_source",
                entity_id=self.source.id,
                payload={
                    "external_source_id": self.source.id,
                    "oauth_connection_id": "connection-a",
                },
                attempt_count=1,
                lease_owner="worker",
            ),
            dependencies=dependencies,
            shutdown_requested=Event(),
            cancellation_requested=Event(),
            logger=logging.LoggerAdapter(logging.getLogger(__name__), {}),
        )

    @staticmethod
    def _provider(
        provider: FakeGoogleProvider, provider_name: str, token: str
    ) -> FakeGoogleProvider:
        if provider_name != "google-drive":
            raise AssertionError(provider_name)
        if token != "access-token":
            raise AssertionError("unexpected access token")
        return provider

    def test_first_run_reconciles_and_later_run_is_incremental(self) -> None:
        first_provider = FakeGoogleProvider()
        first = SourceSyncJobHandler(self.settings)(self.context(first_provider))
        self.assertEqual(first.outcome, JobOutcome.COMPLETED)
        self.assertTrue(first_provider.inputs[0].reconciliation)

        later_provider = FakeGoogleProvider()
        later = SourceSyncJobHandler(self.settings)(self.context(later_provider))
        self.assertEqual(later.outcome, JobOutcome.COMPLETED)
        self.assertFalse(later_provider.inputs[0].reconciliation)


if __name__ == "__main__":
    unittest.main()
