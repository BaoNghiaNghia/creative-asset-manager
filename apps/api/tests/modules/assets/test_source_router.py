import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app.modules.assets.source_router import sync_source


class SourceSyncEndpointTest(unittest.TestCase):
    def test_sync_is_bound_to_the_callers_tenant_and_exact_source(self):
        result = SimpleNamespace(
            source_id="source-b", job_id="job-b", mode="incremental",
            created=True, skipped_reason=None,
        )
        with patch("app.modules.assets.source_router.SourceSyncScheduler") as scheduler:
            scheduler.return_value.enqueue_source.return_value = result
            response = sync_source(
                "source-b", SimpleNamespace(active_tenant_id="tenant-a")
            )

        scheduler.return_value.enqueue_source.assert_called_once_with("tenant-a", "source-b")
        self.assertEqual(response.source_id, "source-b")
        self.assertEqual(response.job_id, "job-b")
        self.assertTrue(response.queued)

    def test_sync_reports_reconnect_requirement_without_cross_source_fallback(self):
        result = SimpleNamespace(
            source_id="source-b", job_id=None, mode=None,
            created=False, skipped_reason="credentials_unavailable",
        )
        with patch("app.modules.assets.source_router.SourceSyncScheduler") as scheduler:
            scheduler.return_value.enqueue_source.return_value = result
            with self.assertRaises(HTTPException) as raised:
                sync_source("source-b", SimpleNamespace(active_tenant_id="tenant-a"))

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail["code"], "source_credentials_unavailable")
