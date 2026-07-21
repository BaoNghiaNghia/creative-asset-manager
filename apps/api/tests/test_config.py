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
    "OPENAI_AI_ENABLED",
    "SEARCH_PROJECTION_ENABLED",
    "ELASTICSEARCH_V2_ENABLED",
    "SEARCH_QUERY_PARSER_V2_ENABLED",
    "EXTERNAL_INGESTION_API_ENABLED",
    "DRIVE_METADATA_SIDECAR_ENABLED",
    "AI_EMERGENCY_STOP_ENABLED",
    "AI_BATCH_FALLBACK_TO_SINGLE_ENABLED",
    "RETENTION_CLEANUP_ENABLED",
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
    def test_invalid_processing_policy_cache_ttl_fails_validation(self) -> None:
        with patch.dict(os.environ, {"PROCESSING_POLICY_CACHE_TTL_SECONDS": "0"}, clear=True):
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


    def test_invalid_batch_limits_fail_validation(self) -> None:
        for name, value in (
            ("AI_BATCH_MAX_ITEMS", "0"),
            ("AI_BATCH_MAX_REQUEST_BYTES", "0"),
            ("AI_BATCH_MINIMUM_AGE_SECONDS", "-1"),
            ("AI_BATCH_POLL_INTERVAL_SECONDS", "0"),
            ("AI_BATCH_MAX_ITEM_ATTEMPTS", "0"),
        ):
            with self.subTest(setting=name):
                with patch.dict(os.environ, {name: value}, clear=True):
                    with self.assertRaises(ValidationError):
                        Settings()

    def test_gemini_key_is_required_when_batch_analysis_is_enabled(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DYNAMIC_AI_METADATA_ENABLED": "true",
                "AI_BATCH_ANALYSIS_ENABLED": "true",
            },
            clear=True,
        ):
            with self.assertRaises(ValidationError):
                Settings()
            os.environ["GEMINI_API_KEY"] = "test-only"
            settings = Settings()
        self.assertEqual(settings.GEMINI_API_KEY, "test-only")

    def test_external_ingestion_requires_sensitive_url_encryption_key(self) -> None:
        with patch.dict(os.environ, {"EXTERNAL_INGESTION_API_ENABLED": "true"}, clear=True):
            with self.assertRaises(ValidationError):
                Settings()
        with patch.dict(os.environ, {
            "EXTERNAL_INGESTION_API_ENABLED": "true",
            "SENSITIVE_URL_ENCRYPTION_KEYS": "v1:eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg=",
        }, clear=True):
            self.assertTrue(Settings().EXTERNAL_INGESTION_API_ENABLED)

    def test_cached_settings_use_the_central_environment_source(self) -> None:
        with patch.dict(
            os.environ,
            {"SEARCH_PROJECTION_ENABLED": "true"},
            clear=True,
        ):
            get_settings.cache_clear()
            self.assertIs(get_settings().SEARCH_PROJECTION_ENABLED, True)

    def test_http_settings_parse_development_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings()
        self.assertEqual(settings.PUBLIC_APP_URL, "http://localhost:5173")
        self.assertEqual(settings.cors_allowed_origins, ("http://localhost:5173",))
        self.assertEqual(
            settings.trusted_hosts, ("localhost", "127.0.0.1", "testserver")
        )
        self.assertTrue(settings.API_DOCS_ENABLED)

    def test_valid_production_http_configuration(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(
                APP_ENV="production",
                PUBLIC_APP_URL="https://assets.example.com",
                CORS_ALLOWED_ORIGINS="https://assets.example.com",
                TRUSTED_HOSTS="api.example.com,*.api.example.com",
                API_DOCS_ENABLED=False,
                DATABASE_URL="postgresql+psycopg://cam:test@db/cam",
            )
        self.assertEqual(
            settings.cors_allowed_origins, ("https://assets.example.com",)
        )
        self.assertFalse(settings.API_DOCS_ENABLED)

    def test_invalid_production_http_configuration_fails_closed(self) -> None:
        valid = {
            "APP_ENV": "production",
            "PUBLIC_APP_URL": "https://assets.example.com",
            "CORS_ALLOWED_ORIGINS": "https://assets.example.com",
            "TRUSTED_HOSTS": "api.example.com",
            "API_DOCS_ENABLED": False,
            "DATABASE_URL": "postgresql+psycopg://cam:test@db/cam",
        }
        invalid_overrides = (
            {"DATABASE_URL": None},
            {"DATABASE_URL": "sqlite:///production.db"},
            {"PUBLIC_APP_URL": "http://assets.example.com"},
            {"PUBLIC_APP_URL": "https://localhost"},
            {"API_DOCS_ENABLED": True},
            {"TRUSTED_HOSTS": "*"},
            {"TRUSTED_HOSTS": "localhost"},
            {"CORS_ALLOWED_ORIGINS": "*"},
            {"CORS_ALLOWED_ORIGINS": "https://*.example.com"},
            {"CORS_ALLOWED_ORIGINS": "http://assets.example.com"},
            {"CORS_ALLOWED_ORIGINS": "https://other.example.com"},
        )
        for override in invalid_overrides:
            with self.subTest(override=override):
                with patch.dict(os.environ, {}, clear=True):
                    with self.assertRaises(ValidationError):
                        Settings(**{**valid, **override})

    def test_cors_origins_and_trusted_hosts_reject_url_components(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValidationError):
                Settings(CORS_ALLOWED_ORIGINS="https://app.example.com/path")
            with self.assertRaises(ValidationError):
                Settings(TRUSTED_HOSTS="https://api.example.com")
            with self.assertRaises(ValidationError):
                Settings(TRUSTED_HOSTS="api.example.com:443")
            with self.assertRaises(ValidationError):
                Settings(API_DOCS_ENABLED="sometimes")


    def test_openai_is_disabled_and_non_retaining_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings()
        self.assertFalse(settings.OPENAI_AI_ENABLED)
        self.assertFalse(settings.OPENAI_STORE_RESPONSES)
        self.assertIsNone(settings.OPENAI_API_KEY)

    def test_openai_enabled_requires_key_model_and_allowlist(self) -> None:
        incomplete_values = (
            {"OPENAI_AI_ENABLED": "true"},
            {
                "OPENAI_AI_ENABLED": "true",
                "OPENAI_API_KEY": "test-only",
            },
            {
                "OPENAI_AI_ENABLED": "true",
                "OPENAI_API_KEY": "test-only",
                "OPENAI_DEFAULT_MODEL": "openai-test",
                "OPENAI_ALLOWED_MODELS": "another-model",
            },
        )
        for values in incomplete_values:
            with self.subTest(values=tuple(values)):
                with patch.dict(os.environ, values, clear=True):
                    with self.assertRaises(ValidationError):
                        Settings()

    def test_openai_valid_configuration_supports_single_analysis(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENAI_AI_ENABLED": "true",
                "OPENAI_API_KEY": "test-only",
                "OPENAI_DEFAULT_MODEL": "openai-test",
                "OPENAI_ALLOWED_MODELS": "openai-test, openai-other",
                "DYNAMIC_AI_METADATA_ENABLED": "true",
                "AI_SINGLE_ANALYSIS_ENABLED": "true",
            },
            clear=True,
        ):
            settings = Settings()
        self.assertEqual(settings.openai_allowed_models, (
            "openai-test", "openai-other"
        ))
        self.assertIsNone(settings.GEMINI_API_KEY)

    def test_openai_runtime_limits_are_validated(self) -> None:
        base = {
            "OPENAI_AI_ENABLED": "true",
            "OPENAI_API_KEY": "test-only",
            "OPENAI_DEFAULT_MODEL": "openai-test",
            "OPENAI_ALLOWED_MODELS": "openai-test",
        }
        for override in (
            {"OPENAI_TIMEOUT_SECONDS": "0"},
            {"OPENAI_MAX_RETRIES": "-1"},
            {"OPENAI_IMAGE_DETAIL": "unbounded"},
        ):
            with self.subTest(override=override):
                with patch.dict(
                    os.environ, {**base, **override}, clear=True
                ):
                    with self.assertRaises(ValidationError):
                        Settings()
if __name__ == "__main__":
    unittest.main()
