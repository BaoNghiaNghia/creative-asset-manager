from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.core.config import Settings
from app.domain.processing.types import JOB_TYPES
from app.modules.processing.bootstrap import build_worker_runtime, default_worker_id
from app.modules.processing.worker_roles import (
    IMAGE_WORKER_JOB_TYPES,
    VIDEO_WORKER_JOB_TYPES,
    allowed_job_types_for_role,
)


class WorkerRoleTest(unittest.TestCase):
    def test_settings_defaults_to_all_role(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Settings().WORKER_ROLE, "all")

    def test_settings_normalizes_worker_roles(self) -> None:
        self.assertEqual(Settings(WORKER_ROLE=" IMAGE ").WORKER_ROLE, "image")
        self.assertEqual(Settings(WORKER_ROLE="video").WORKER_ROLE, "video")

    def test_invalid_role_fails_fast(self) -> None:
        with self.assertRaisesRegex(ValidationError, "WORKER_ROLE must be one of"):
            Settings(WORKER_ROLE="batch")

    def test_canonical_roles_cover_registered_job_types_without_video_overlap(self) -> None:
        self.assertEqual(allowed_job_types_for_role("all"), JOB_TYPES)
        self.assertEqual(set(IMAGE_WORKER_JOB_TYPES) | set(VIDEO_WORKER_JOB_TYPES), set(JOB_TYPES))
        self.assertFalse(set(IMAGE_WORKER_JOB_TYPES) & set(VIDEO_WORKER_JOB_TYPES))
        self.assertEqual(VIDEO_WORKER_JOB_TYPES, ("video_analyze", "video_search_index"))

    def test_image_role_excludes_video_jobs(self) -> None:
        allowed = allowed_job_types_for_role("image")
        self.assertNotIn("video_analyze", allowed)
        self.assertNotIn("video_search_index", allowed)
        self.assertIn("asset_analyze", allowed)
        self.assertIn("asset_index", allowed)
        self.assertIn("search_projection_build", allowed)

    def test_video_role_excludes_non_video_jobs(self) -> None:
        allowed = allowed_job_types_for_role("video")
        self.assertEqual(allowed, VIDEO_WORKER_JOB_TYPES)
        self.assertNotIn("asset_analyze", allowed)
        self.assertNotIn("asset_index", allowed)
        self.assertNotIn("search_projection_build", allowed)

    def test_default_worker_ids_include_the_role(self) -> None:
        self.assertTrue(default_worker_id("image").startswith("creativeasset-image-"))
        self.assertTrue(default_worker_id("video").startswith("creativeasset-video-"))

    def test_runtime_keeps_all_role_compatible_when_processing_disabled(self) -> None:
        runtime = build_worker_runtime(Settings(PROCESSING_JOBS_ENABLED=False))
        try:
            self.assertEqual(runtime.config.worker_role, "all")
            self.assertEqual(runtime.config.allowed_job_types, ())
        finally:
            runtime.close()


if __name__ == "__main__":
    unittest.main()
