from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.core.config import Settings
from app.domain.processing.types import JOB_TYPES
from app.modules.processing.bootstrap import build_worker_runtime, default_worker_id
from app.modules.processing.worker_roles import (
    IMAGE_AI_JOB_TYPES,
    IMAGE_WORKER_JOB_TYPES,
    VIDEO_AI_JOB_TYPES,
    VIDEO_DELIVERY_JOB_TYPES,
    VIDEO_HEAVY_JOB_TYPES,
    VIDEO_WORKER_JOB_TYPES,
    VISUAL_WORKER_JOB_TYPES,
    allowed_job_types_for_role,
    borrowable_job_types_for_role,
    runs_video_cache_scheduler,
)


class WorkerRoleTest(unittest.TestCase):
    def test_settings_defaults_to_all_role(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Settings().WORKER_ROLE, "all")

    def test_settings_normalizes_worker_roles(self) -> None:
        self.assertEqual(Settings(WORKER_ROLE=" IMAGE ").WORKER_ROLE, "image")
        self.assertEqual(Settings(WORKER_ROLE="video").WORKER_ROLE, "video")
        self.assertEqual(Settings(WORKER_ROLE=" VIDEO-HEAVY ").WORKER_ROLE, "video-heavy")
        self.assertEqual(Settings(WORKER_ROLE="video-delivery").WORKER_ROLE, "video-delivery")
        self.assertEqual(Settings(WORKER_ROLE="visual").WORKER_ROLE, "visual")

    def test_invalid_role_fails_fast(self) -> None:
        with self.assertRaisesRegex(ValidationError, "WORKER_ROLE must be one of"):
            Settings(WORKER_ROLE="batch")

    def test_canonical_roles_cover_registered_job_types_without_video_overlap(self) -> None:
        self.assertEqual(allowed_job_types_for_role("all"), JOB_TYPES)
        self.assertEqual(
            set(IMAGE_WORKER_JOB_TYPES) | set(VIDEO_WORKER_JOB_TYPES) | set(VISUAL_WORKER_JOB_TYPES),
            set(JOB_TYPES),
        )
        self.assertFalse(set(IMAGE_WORKER_JOB_TYPES) & set(VIDEO_WORKER_JOB_TYPES))
        self.assertFalse(set(IMAGE_WORKER_JOB_TYPES) & set(VISUAL_WORKER_JOB_TYPES))
        self.assertEqual(
            VIDEO_WORKER_JOB_TYPES,
            (
                "video_analyze", "video_search_index", "video_generate",
                "video_cache_fill", "video_playback_prepare",
            ),
        )

    def test_image_role_excludes_video_jobs(self) -> None:
        allowed = allowed_job_types_for_role("image")
        self.assertNotIn("video_analyze", allowed)
        self.assertNotIn("video_search_index", allowed)
        self.assertNotIn("visual_index_sync", allowed)
        self.assertNotIn("video_generate", allowed)
        self.assertNotIn("video_cache_fill", allowed)
        self.assertIn("asset_analyze", allowed)
        self.assertIn("asset_index", allowed)
        self.assertIn("search_projection_build", allowed)

    def test_video_role_excludes_non_video_jobs(self) -> None:
        allowed = allowed_job_types_for_role("video")
        self.assertEqual(allowed, VIDEO_WORKER_JOB_TYPES)
        self.assertIn("video_generate", allowed)
        self.assertIn("video_cache_fill", allowed)
        self.assertNotIn("asset_analyze", allowed)
        self.assertNotIn("asset_index", allowed)
        self.assertNotIn("search_projection_build", allowed)

    def test_production_video_lanes_do_not_overlap(self) -> None:
        self.assertEqual(
            VIDEO_HEAVY_JOB_TYPES,
            ("video_analyze", "video_generate"),
        )
        self.assertEqual(
            VIDEO_DELIVERY_JOB_TYPES,
            ("video_search_index", "video_cache_fill", "video_playback_prepare"),
        )
        self.assertFalse(set(VIDEO_HEAVY_JOB_TYPES) & set(VIDEO_DELIVERY_JOB_TYPES))
        self.assertEqual(
            allowed_job_types_for_role("video-heavy"),
            VIDEO_HEAVY_JOB_TYPES,
        )
        self.assertEqual(
            allowed_job_types_for_role("video-delivery"),
            VIDEO_DELIVERY_JOB_TYPES,
        )

    def test_dedicated_roles_only_borrow_peer_ai_work(self) -> None:
        enabled = JOB_TYPES
        self.assertEqual(borrowable_job_types_for_role("image", enabled), VIDEO_AI_JOB_TYPES)
        self.assertEqual(borrowable_job_types_for_role("video", enabled), IMAGE_AI_JOB_TYPES)
        self.assertEqual(borrowable_job_types_for_role("video-heavy", enabled), ())
        self.assertEqual(borrowable_job_types_for_role("video-delivery", enabled), ())
        self.assertEqual(borrowable_job_types_for_role("all", enabled), ())
        self.assertEqual(borrowable_job_types_for_role("visual", enabled), ())

    def test_video_cache_scheduler_runs_once_in_production_topology(self) -> None:
        self.assertTrue(runs_video_cache_scheduler("video-delivery"))
        self.assertTrue(runs_video_cache_scheduler("all"))
        self.assertFalse(runs_video_cache_scheduler("image"))
        self.assertFalse(runs_video_cache_scheduler("video-heavy"))
        self.assertFalse(runs_video_cache_scheduler("video"))

    def test_visual_role_only_claims_visual_index_jobs(self) -> None:
        self.assertEqual(
            allowed_job_types_for_role("visual"),
            VISUAL_WORKER_JOB_TYPES,
        )
        self.assertEqual(VISUAL_WORKER_JOB_TYPES, ("visual_index_sync",))

    def test_default_worker_ids_include_the_role(self) -> None:
        self.assertTrue(default_worker_id("image").startswith("creativeasset-image-"))
        self.assertTrue(default_worker_id("video").startswith("creativeasset-video-"))
        self.assertTrue(default_worker_id("video-heavy").startswith("creativeasset-video-heavy-"))
        self.assertTrue(default_worker_id("video-delivery").startswith("creativeasset-video-delivery-"))
        self.assertTrue(default_worker_id("visual").startswith("creativeasset-visual-"))

    def test_runtime_keeps_all_role_compatible_when_processing_disabled(self) -> None:
        runtime = build_worker_runtime(Settings(PROCESSING_JOBS_ENABLED=False))
        try:
            self.assertEqual(runtime.config.worker_role, "all")
            self.assertEqual(runtime.config.allowed_job_types, ())
        finally:
            runtime.close()

    def test_runtime_accepts_visual_role(self) -> None:
        runtime = build_worker_runtime(
            Settings(PROCESSING_JOBS_ENABLED=False, WORKER_ROLE="visual")
        )
        try:
            self.assertEqual(runtime.config.worker_role, "visual")
            self.assertEqual(runtime.config.allowed_job_types, ())
        finally:
            runtime.close()


if __name__ == "__main__":
    unittest.main()
