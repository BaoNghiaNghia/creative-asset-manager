import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.modules.pipeline.stages import ProviderDownloadStage, ProviderStorageStage
from app.modules.processing.bootstrap import build_worker_runtime


class PipelineBootstrapTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{Path(self.directory.name) / 'worker.db'}"
        )
        self.sessions = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.directory.cleanup()

    def test_worker_registers_pipeline_download_stage(self):
        runtime = build_worker_runtime(
            Settings(PROCESSING_JOBS_ENABLED=False),
            session_factory=self.sessions,
        )
        try:
            self.assertIsInstance(
                runtime.dependencies.resources["pipeline_download_stage"],
                ProviderDownloadStage,
            )
        finally:
            runtime.close()

    def test_static_storage_access_token_is_not_used(self):
        runtime = build_worker_runtime(
            Settings(
                PROCESSING_JOBS_ENABLED=False,
                GOOGLE_MANAGED_STORAGE_ACCESS_TOKEN="test-token",
                GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID="root-folder",
            ),
            session_factory=self.sessions,
        )
        try:
            self.assertNotIn(
                "pipeline_storage_stage",
                runtime.dependencies.resources,
            )
        finally:
            runtime.close()

    def test_explicit_resources_override_pipeline_defaults(self):
        download = object()
        storage = object()
        runtime = build_worker_runtime(
            Settings(PROCESSING_JOBS_ENABLED=False),
            session_factory=self.sessions,
            resources={
                "pipeline_download_stage": download,
                "pipeline_storage_stage": storage,
            },
        )
        try:
            self.assertIs(
                runtime.dependencies.resources["pipeline_download_stage"],
                download,
            )
            self.assertIs(
                runtime.dependencies.resources["pipeline_storage_stage"],
                storage,
            )
        finally:
            runtime.close()

    def test_static_storage_refresh_token_is_not_used(self):
        runtime = build_worker_runtime(
            Settings(
                PROCESSING_JOBS_ENABLED=False,
                GOOGLE_CLIENT_ID="client-id",
                GOOGLE_CLIENT_SECRET="client-secret",
                GOOGLE_MANAGED_STORAGE_REFRESH_TOKEN="refresh-token",
                GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID="root-folder",
            ),
            session_factory=self.sessions,
        )
        try:
            self.assertNotIn(
                "pipeline_storage_stage",
                runtime.dependencies.resources,
            )
        finally:
            runtime.close()

    def test_unconfigured_managed_storage_does_not_stop_the_worker(self):
        runtime = build_worker_runtime(
            Settings(
                PROCESSING_JOBS_ENABLED=True,
                MANAGED_ASSET_STORAGE_ENABLED=True,
            ),
            session_factory=self.sessions,
        )
        try:
            self.assertNotIn("asset_store", runtime.config.allowed_job_types)
            self.assertNotIn(
                "pipeline_storage_stage",
                runtime.dependencies.resources,
            )
        finally:
            runtime.close()


if __name__ == "__main__":
    unittest.main()
