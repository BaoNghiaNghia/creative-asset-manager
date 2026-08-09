import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.providers.ai.gemini import GeminiModelLimit


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
    "INVENTORY_AUTOMATION_ENABLED",
    "INVENTORY_WORKER_ENABLED",
    "INVENTORY_DRIVE_POLLER_ENABLED",
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

    def test_search_v3_safety_defaults_are_conservative(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings()
        self.assertIs(settings.SEARCH_V3_REQUIRED, True)

    def test_inventory_worker_requires_the_default_off_automation_boundary(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(INVENTORY_WORKER_ENABLED=True)
        settings = Settings(
            INVENTORY_AUTOMATION_ENABLED=True,
            INVENTORY_WORKER_ENABLED=True,
        )
        self.assertTrue(settings.INVENTORY_WORKER_ENABLED)

    def test_inventory_drive_poller_requires_worker_and_automation(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(INVENTORY_DRIVE_POLLER_ENABLED=True)
        with self.assertRaises(ValidationError):
            Settings(
                INVENTORY_AUTOMATION_ENABLED=True,
                INVENTORY_DRIVE_POLLER_ENABLED=True,
            )
        settings = Settings(
            INVENTORY_AUTOMATION_ENABLED=True,
            INVENTORY_WORKER_ENABLED=True,
            INVENTORY_DRIVE_POLLER_ENABLED=True,

        )
        self.assertTrue(settings.INVENTORY_DRIVE_POLLER_ENABLED)
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

    def test_rate_limit_safety_margin_defaults_and_rejects_negative_values(self) -> None:
        settings = Settings()
        self.assertEqual(settings.AI_JOB_RATE_LIMIT_SAFETY_SECONDS, 0.5)
        with self.assertRaises(ValidationError):
            Settings(AI_JOB_RATE_LIMIT_SAFETY_SECONDS=-0.1)

    def test_managed_storage_refresh_credentials_load_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GOOGLE_CLIENT_ID": "client-id",
                "GOOGLE_CLIENT_SECRET": "client-secret",
                "GOOGLE_MANAGED_STORAGE_REFRESH_TOKEN": "refresh-token",
            },
            clear=True,
        ):
            settings = Settings()

        self.assertEqual(settings.GOOGLE_CLIENT_ID, "client-id")
        self.assertEqual(settings.GOOGLE_CLIENT_SECRET, "client-secret")
        self.assertEqual(
            settings.GOOGLE_MANAGED_STORAGE_REFRESH_TOKEN,
            "refresh-token",
        )

    def test_gemini_model_pool_defaults_and_configured_limits(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            defaults = Settings()
        self.assertEqual(
            defaults.gemini_model_pool,
            (
                "gemini-3.5-flash-lite",
                "gemini-3.1-flash-lite",
                "gemini-3.6-flash",
                "gemini-2.5-flash-lite",
                "gemini-2.5-flash",
            ),
        )
        self.assertEqual(
            defaults.gemini_model_limits["gemini-2.5-flash-lite"],
            GeminiModelLimit(rpm=8, tpm=200000, rpd=16),
        )
        configured = Settings(
            GEMINI_MODEL_POOL="one,two",
            GEMINI_MODEL_LIMITS='{"one":{"rpm":2,"tpm":200,"rpd":3},"two":{"rpm":4,"tpm":500,"rpd":5}}',
        )
        self.assertEqual(
            configured.gemini_model_limits,
            {
                "one": GeminiModelLimit(rpm=2, tpm=200, rpd=3),
                "two": GeminiModelLimit(rpm=4, tpm=500, rpd=5),
            },
        )
        self.assertEqual(configured.gemini_project_daily_request_limit, 8)
        self.assertEqual(
            Settings(GEMINI_PROJECT_DAILY_REQUEST_LIMIT=7).gemini_project_daily_request_limit,
            7,
        )

    def test_search_suggestion_performance_defaults_and_validation(self) -> None:
        settings = Settings()
        self.assertEqual(settings.SEARCH_SUGGESTIONS_REQUEST_TIMEOUT_SECONDS, 0.8)
        self.assertEqual(settings.SEARCH_SUGGESTIONS_QUERY_TIMEOUT_MS, 300)
        self.assertEqual(settings.SEARCH_SUGGESTIONS_CACHE_TTL_SECONDS, 45)
        with self.assertRaises(ValidationError):
            Settings(SEARCH_SUGGESTIONS_REQUEST_TIMEOUT_SECONDS=0)
        with self.assertRaises(ValidationError):
            Settings(SEARCH_SUGGESTIONS_QUERY_TIMEOUT_MS=0)
        with self.assertRaises(ValidationError):
            Settings(SEARCH_SUGGESTIONS_CACHE_TTL_SECONDS=0)

    def test_per_model_rpm_configuration_is_provider_scoped(self) -> None:
        settings = Settings(
            AI_MODEL_RPM_LIMITS='{"openai":{"gpt-4.1-mini":3}}',
            AI_MODEL_RPM_GEMINI_2_5_FLASH=5,
        )
        self.assertEqual(settings.ai_model_rpm("gemini", "gemini-2.5-flash"), 5)
        self.assertEqual(settings.ai_model_rpm("openai", "gpt-4.1-mini"), 3)
        self.assertIsNone(settings.ai_model_rpm("openai", "other-model"))
        with self.assertRaises(ValidationError):
            Settings(AI_MODEL_RPM_LIMITS='{"openai":{"gpt-4.1-mini":0}}')

    def test_gemini_model_configuration_rejects_whitespace_and_unknown_rpm_models(self) -> None:
        with self.assertRaises(ValueError):
            Settings(
                GEMINI_MODEL_LIMITS='{"gemini-2.5- flash-lite":{"rpm":8,"tpm":200000,"rpd":16}}'
            ).gemini_model_limits
        with self.assertRaises(ValueError):
            Settings(
                AI_MODEL_RPM_LIMITS='{"gemini":{"gemini-unknown":5}}'
            ).ai_model_rpm_limits
        valid = Settings(
            GEMINI_MODEL_POOL="gemini-2.5-flash",
            GEMINI_MODEL_LIMITS='{"gemini-2.5-flash":{"rpm":4,"tpm":200000,"rpd":16}}',
            AI_MODEL_RPM_LIMITS='{"gemini":{"gemini-2.5-flash":4}}',
        )
        self.assertEqual(valid.ai_model_rpm("gemini", "gemini-2.5-flash"), 4)

    def test_invalid_gemini_model_limits_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            Settings(
                GEMINI_MODEL_LIMITS='{"gemini-3.5-flash-lite":{"rpm":12,"rpd":400}}'
            )
        with self.assertRaises(ValueError):
            Settings(
                GEMINI_MODEL_POOL="one,two",
                GEMINI_MODEL_LIMITS='{"one":{"rpm":2,"tpm":200,"rpd":3}}',
            )


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
            ("AI_ANALYSIS_BULK_MAX_ITEMS", "0"),
            ("AI_ANALYSIS_BULK_MAX_ITEMS", "1001"),
            ("AI_ANALYSIS_BULK_MAX_PAYLOAD_BYTES", "0"),
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

    def test_openai_batch_can_satisfy_batch_provider_validation(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DYNAMIC_AI_METADATA_ENABLED": "true",
                "AI_BATCH_ANALYSIS_ENABLED": "true",
                "OPENAI_AI_ENABLED": "true",
                "OPENAI_BATCH_ENABLED": "true",
                "OPENAI_API_KEY": "test-only",
                "OPENAI_DEFAULT_MODEL": "openai-test",
                "OPENAI_ALLOWED_MODELS": "openai-test",
            },
            clear=True,
        ):
            settings = Settings()
        self.assertTrue(settings.OPENAI_BATCH_ENABLED)

    def test_openai_batch_limits_are_validated(self) -> None:
        with patch.dict(os.environ, {
            "OPENAI_BATCH_COMPLETION_WINDOW": "1h"}, clear=True):
            with self.assertRaises(ValidationError):
                Settings()
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

    def test_gemini_model_must_be_server_allowlisted(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GEMINI_MODEL": "browser-model",
                "GEMINI_ALLOWED_MODELS": "gemini-test",
            },
            clear=True,
        ):
            with self.assertRaises(ValidationError):
                Settings()

if __name__ == "__main__":
    unittest.main()
