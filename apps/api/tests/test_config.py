import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.core.config import Settings, get_settings


FEATURE_FLAGS = (
    "UNIFIED_ASSET_INGESTION_ENABLED",
    "CONTENT_DEDUP_ENABLED",
    "INCREMENTAL_SOURCE_SYNC_ENABLED",
    "PROCESSING_JOBS_ENABLED",
    "EXTERNAL_ASSET_DOWNLOADER_ENABLED",
    "MANAGED_ASSET_STORAGE_ENABLED",
    "DYNAMIC_AI_METADATA_ENABLED",
    "AI_SINGLE_ANALYSIS_ENABLED",
    "AI_BATCH_ANALYSIS_ENABLED",
    "AI_AUTO_ANALYZE_ENABLED",
    "SEARCH_PROJECTION_ENABLED",
    "ELASTICSEARCH_V2_ENABLED",
    "SEARCH_QUERY_PARSER_V2_ENABLED",
    "EXTERNAL_INGESTION_API_ENABLED",
    "DRIVE_METADATA_SIDECAR_ENABLED",
)


class SettingsTest(unittest.TestCase):
    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_all_feature_flags_default_to_false(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings()

        for name in FEATURE_FLAGS:
            with self.subTest(flag=name):
                self.assertIs(getattr(settings, name), False)

    def test_explicit_true_and_false_values_are_accepted(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CONTENT_DEDUP_ENABLED": "true",
                "ELASTICSEARCH_V2_ENABLED": "FALSE",
            },
            clear=True,
        ):
            settings = Settings()

        self.assertIs(settings.CONTENT_DEDUP_ENABLED, True)
        self.assertIs(settings.ELASTICSEARCH_V2_ENABLED, False)

    def test_invalid_feature_flag_fails_validation(self) -> None:
        with patch.dict(
            os.environ,
            {"PROCESSING_JOBS_ENABLED": "sometimes"},
            clear=True,
        ):
            with self.assertRaises(ValidationError):
                Settings()

    def test_invalid_worker_heartbeat_fails_validation(self) -> None:
        with patch.dict(
            os.environ,
            {
                "WORKER_LEASE_SECONDS": "10",
                "WORKER_HEARTBEAT_SECONDS": "10",
            },
            clear=True,
        ):
            with self.assertRaises(ValidationError):
                Settings()
    def test_gemini_key_is_required_only_when_single_analysis_is_enabled(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DYNAMIC_AI_METADATA_ENABLED": "true",
                "AI_SINGLE_ANALYSIS_ENABLED": "true",
            },
            clear=True,
        ):
            with self.assertRaises(ValidationError):
                Settings()
            os.environ["GEMINI_API_KEY"] = "test-only"
            settings = Settings()
        self.assertEqual(settings.GEMINI_API_KEY, "test-only")


    def test_cached_settings_use_the_central_environment_source(self) -> None:
        with patch.dict(
            os.environ,
            {"SEARCH_PROJECTION_ENABLED": "true"},
            clear=True,
        ):
            get_settings.cache_clear()
            self.assertIs(get_settings().SEARCH_PROJECTION_ENABLED, True)


if __name__ == "__main__":
    unittest.main()
