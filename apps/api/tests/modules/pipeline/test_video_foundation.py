import asyncio
import unittest
from types import SimpleNamespace

from app.core.config import Settings
from app.modules.pipeline.mime_types import (
    is_eligible_video_source_asset,
    is_supported_google_drive_video_mime_type,
    is_supported_video_mime_type,
)
from app.providers.google.incremental import _candidate


class VideoMimeFoundationTest(unittest.TestCase):
    def test_supported_video_mimes_are_limited_to_v1(self):
        self.assertTrue(is_supported_google_drive_video_mime_type("video/mp4"))
        self.assertTrue(is_supported_video_mime_type("video/quicktime"))
        self.assertFalse(is_supported_video_mime_type("video/webm"))
        self.assertTrue(is_eligible_video_source_asset(SimpleNamespace(
            mime_type="video/mp4", deleted_at=None,
        )))
        self.assertFalse(is_eligible_video_source_asset(SimpleNamespace(
            mime_type="video/mp4", deleted_at=object(),
        )))

    def test_video_flags_are_disabled_by_default(self):
        settings = Settings()
        self.assertFalse(settings.VIDEO_SEARCH_ENABLED)
        self.assertFalse(settings.VIDEO_ANALYSIS_ENABLED)
        self.assertFalse(settings.VIDEO_PROXY_ENABLED)

    def test_incremental_candidate_keeps_video_metadata(self):
        candidate = _candidate({
            "id": "video-1", "name": "clip.mp4", "mimeType": "video/mp4",
            "videoMediaMetadata": {"width": 1920, "height": 1080, "durationMillis": "42000"},
        }, "source-1")
        self.assertEqual(candidate.source_metadata["video_width"], 1920)
        self.assertEqual(candidate.source_metadata["video_height"], 1080)
        self.assertEqual(candidate.source_metadata["video_duration_ms"], "42000")
