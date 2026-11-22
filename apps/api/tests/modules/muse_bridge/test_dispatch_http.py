"""HTTP contract for Muse command long polling; no production DB access."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.muse_bridge import router as muse


TOKEN = "t" * 64
PATH = "/api/v1/muse-bridge/commands/wait"
EMPTY = {
    "ok": True, "items": [], "count": 0,
    "pollAfterSeconds": 20,
    "longPollPath": "/api/v1/muse-bridge/commands/wait?waitSeconds=20",
}


class MuseLongPollHttpTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(muse.router)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.config = patch.object(muse, "_configured", return_value=(TOKEN, "tenant-a"))
        self.config.start()
        self.addCleanup(self.config.stop)
        self.addCleanup(self.client.close)

    def headers(self):
        return {"Authorization": "Bearer " + TOKEN}

    def test_missing_and_wrong_bearer_rejected(self):
        for headers in ({}, {"Authorization": "Bearer " + "x" * 64}):
            with self.subTest(headers=headers):
                result = self.client.get(PATH + "?waitSeconds=0", headers=headers)
                self.assertEqual(result.status_code, 401)

    def test_ready_job_returns_without_claim_and_is_uncacheable(self):
        ready = {**EMPTY, "items": [{"commandId": "test-id"}], "count": 1}
        with patch.object(muse, "_read_muse_commands_for_long_poll", return_value=ready) as get:
            result = self.client.get(PATH + "?waitSeconds=20", headers=self.headers())
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.headers["cache-control"], "private, no-store")
        self.assertEqual(result.json()["items"], ready["items"])
        self.assertEqual(result.json()["longPoll"], True)
        get.assert_called_once_with("tenant-a", 1)

    def test_empty_queue_zero_wait_and_validation(self):
        with patch.object(muse, "_read_muse_commands_for_long_poll", return_value=EMPTY) as get:
            result = self.client.get(PATH + "?waitSeconds=0&limit=2", headers=self.headers())
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["items"], [])
        self.assertEqual(result.headers["cache-control"], "private, no-store")
        get.assert_called_once_with("tenant-a", 2)
        for suffix in ("waitSeconds=21", "waitSeconds=-1", "limit=21"):
            with self.subTest(suffix=suffix):
                self.assertEqual(
                    self.client.get(PATH + "?" + suffix, headers=self.headers()).status_code, 422
                )

    def test_unclaimed_response_is_read_only(self):
        # A GET cannot grant a lease: the response contains only discovery data.
        ready = {**EMPTY, "items": [{"commandId": "test-id"}], "count": 1}
        with patch.object(muse, "_read_muse_commands_for_long_poll", return_value=ready):
            response = self.client.get(PATH + "?waitSeconds=0", headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("lease", response.json())
