import ipaddress
import json
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.test_bootstrap import activate_test_environment
from app.providers.ai.gemini import GeminiModelLimit

activate_test_environment()
_BUILD_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_ROLE_KEY_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")

FEATURE_FLAG_NAMES = (
    "UNIFIED_ASSET_INGESTION_ENABLED",
    "CONTENT_DEDUP_ENABLED",
    "INCREMENTAL_SOURCE_SYNC_ENABLED",
    "SOURCE_SYNC_SCHEDULER_ENABLED",
    "GOOGLE_AUTO_SCAN_ON_LOGIN_ENABLED",
    "GOOGLE_FULL_SCAN_ON_FIRST_LOGIN_ENABLED",
    "PROCESSING_JOBS_ENABLED",
    "EXTERNAL_ASSET_DOWNLOADER_ENABLED",
    "MANAGED_ASSET_STORAGE_ENABLED",
    "DYNAMIC_AI_METADATA_ENABLED",
    "AI_SINGLE_ANALYSIS_ENABLED",
    "AI_BATCH_ANALYSIS_ENABLED",
    "AI_AUTO_ANALYZE_ENABLED",
    "OPENAI_AI_ENABLED",
    "OPENAI_BATCH_ENABLED",
    "SEARCH_PROJECTION_ENABLED",
    "ELASTICSEARCH_V2_ENABLED",
    "SEARCH_V3_ENABLED",
    "SEARCH_V3_REQUIRED",
    "SEARCH_QUERY_PARSER_V2_ENABLED",
    "EXTERNAL_INGESTION_API_ENABLED",
    "DRIVE_METADATA_SIDECAR_ENABLED",
    "AI_EMERGENCY_STOP_ENABLED",
    "GEMINI_EMERGENCY_STOP_ENABLED",
    "OPENAI_EMERGENCY_STOP_ENABLED",
    "AI_BATCH_FALLBACK_TO_SINGLE_ENABLED",
    "PERSISTENT_AUTH_ENABLED",
    "RETENTION_CLEANUP_ENABLED",
    "DETERMINISTIC_ACTIVE_ANALYSIS_ENABLED",
    "SEARCH_SHADOW_COMPARISON_ENABLED",
    "ELASTICSEARCH_INDEX_LIFECYCLE_ENABLED",
    "INVENTORY_AUTOMATION_ENABLED",
    "INVENTORY_WORKER_ENABLED",
)


class Settings(BaseSettings):
    """Validated application settings for architecture rollout flags."""

    model_config = SettingsConfigDict(extra="ignore")

    PUBLIC_APP_URL: str = "http://localhost:5173"
    CORS_ALLOWED_ORIGINS: str = "http://localhost:5173"
    TRUSTED_HOSTS: str = "localhost,127.0.0.1,testserver"
    API_DOCS_ENABLED: bool = True
    APP_VERSION: str = "0.4.0"
    BUILD_COMMIT: str = "unknown"
    PROXY_HEADERS_ENABLED: bool = False
    PROXY_TRUSTED_IPS: str = "127.0.0.1,::1"
    HEALTHCHECK_TIMEOUT_SECONDS: float = 2.0
    DATABASE_URL: str | None = None
    DATABASE_POOL_SIZE: int = 5
    DATABASE_MAX_OVERFLOW: int = 10
    DATABASE_POOL_TIMEOUT_SECONDS: float = 30.0
    DATABASE_POOL_RECYCLE_SECONDS: int = 1800
    DATABASE_CONNECT_TIMEOUT_SECONDS: int = 10

    UNIFIED_ASSET_INGESTION_ENABLED: bool = False
    CONTENT_DEDUP_ENABLED: bool = False
    INCREMENTAL_SOURCE_SYNC_ENABLED: bool = False
    SOURCE_SYNC_SCHEDULER_ENABLED: bool = False
    GOOGLE_AUTO_SCAN_ON_LOGIN_ENABLED: bool = False
    GOOGLE_FULL_SCAN_ON_FIRST_LOGIN_ENABLED: bool = False
    PROCESSING_JOBS_ENABLED: bool = False
    EXTERNAL_ASSET_DOWNLOADER_ENABLED: bool = False
    MANAGED_ASSET_STORAGE_ENABLED: bool = False
    DYNAMIC_AI_METADATA_ENABLED: bool = False
    AI_SINGLE_ANALYSIS_ENABLED: bool = False
    AI_BATCH_ANALYSIS_ENABLED: bool = False
    AI_AUTO_ANALYZE_ENABLED: bool = False
    SEARCH_PROJECTION_ENABLED: bool = False
    ELASTICSEARCH_V2_ENABLED: bool = False
    SEARCH_V3_ENABLED: bool = False
    SEARCH_V3_REQUIRED: bool = True
    SEARCH_QUERY_PARSER_V2_ENABLED: bool = False
    EXTERNAL_INGESTION_API_ENABLED: bool = False
    DRIVE_METADATA_SIDECAR_ENABLED: bool = False
    AI_EMERGENCY_STOP_ENABLED: bool = False
    GEMINI_EMERGENCY_STOP_ENABLED: bool = False
    OPENAI_EMERGENCY_STOP_ENABLED: bool = False
    AI_BATCH_FALLBACK_TO_SINGLE_ENABLED: bool = False
    PERSISTENT_AUTH_ENABLED: bool = False
    RETENTION_CLEANUP_ENABLED: bool = False
    DEVELOPMENT_PERSONAL_TENANT_ENABLED: bool = False
    DETERMINISTIC_ACTIVE_ANALYSIS_ENABLED: bool = False
    SEARCH_SHADOW_COMPARISON_ENABLED: bool = False
    ELASTICSEARCH_INDEX_LIFECYCLE_ENABLED: bool = False
    INVENTORY_AUTOMATION_ENABLED: bool = False
    INVENTORY_WORKER_ENABLED: bool = False
    INVENTORY_DRIVE_POLLER_ENABLED: bool = False
    INVENTORY_DAILY_SCHEDULER_ENABLED: bool = False
    INVENTORY_DAILY_SCHEDULER_POLL_SECONDS: int = 30
    # Deployment controls are deny-by-default. Tenant IDs and credentials stay in deployment secrets.
    INVENTORY_TENANT_ALLOWLIST: str = ""
    INVENTORY_SHADOW_MODE: bool = False
    INVENTORY_AI_GEMINI_API_KEY: str | None = None
    INVENTORY_AI_PROJECT_QUOTA_SCOPE: str = "inventory"
    AUTH_PROCESSING_ADMIN_ALLOWLIST_COMPAT_ENABLED: bool = False
    AUTH_SELF_SIGNUP_ENABLED: bool = False
    AI_STORE_RAW_RESPONSE_ENABLED: bool = False
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-3.5-flash-lite"
    GEMINI_TIMEOUT_SECONDS: float = 45.0
    GEMINI_ALLOWED_MODELS: str = "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.6-flash,gemini-2.5-flash-lite,gemini-2.5-flash"
    GEMINI_MODEL_POOL: str = "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.6-flash,gemini-2.5-flash-lite,gemini-2.5-flash"
    GEMINI_MODEL_LIMITS: str = ""
    GEMINI_MODEL_COOLDOWN_SECONDS: float = 60.0
    GEMINI_PROJECT_QUOTA_SCOPE: str = "default"
    GEMINI_PROJECT_DAILY_REQUEST_LIMIT: int | None = None
    AI_MODEL_RPM_LIMITS: str = ""
    AI_MODEL_RPM_GEMINI_2_5_FLASH: int | None = None
    AI_MODEL_RPM_GPT_4_1_MINI: int | None = None
    AI_JOB_MIN_INTERVAL_SECONDS: float = 10.0
    AI_JOB_RATE_LIMIT_SAFETY_SECONDS: float = 0.5
    AI_RATE_LIMIT_429_MAX_RETRIES: int = 8
    AI_RATE_LIMIT_BACKOFF_MAX_SECONDS: float = 300.0
    OPENAI_AI_ENABLED: bool = False
    OPENAI_API_KEY: str | None = None
    OPENAI_BASE_URL: str | None = None
    OPENAI_DEFAULT_MODEL: str = ""
    OPENAI_ALLOWED_MODELS: str = ""
    OPENAI_TIMEOUT_SECONDS: float = 60.0
    OPENAI_MAX_RETRIES: int = 2
    OPENAI_IMAGE_DETAIL: str = "auto"
    OPENAI_STORE_RESPONSES: bool = False
    OPENAI_ORGANIZATION: str | None = None
    OPENAI_PROJECT: str | None = None
    OPENAI_BATCH_ENABLED: bool = False
    OPENAI_BATCH_COMPLETION_WINDOW: str = "24h"
    OPENAI_BATCH_MAX_ITEMS: int = 1000
    OPENAI_BATCH_MAX_FILE_BYTES: int = 150_000_000
    OPENAI_BATCH_POLL_INTERVAL_SECONDS: float = 60.0
    OPENAI_BATCH_RESULT_PAGE_OR_CHUNK_SIZE: int = 65_536
    OPENAI_BATCH_INPUT_RETENTION_HOURS: int = 24
    OPENAI_BATCH_OUTPUT_RETENTION_HOURS: int = 24
    AI_ANALYSIS_MAX_SOURCE_BYTES: int = 25_000_000
    AI_ANALYSIS_MAX_SOURCE_WIDTH: int = 20_000
    AI_ANALYSIS_MAX_SOURCE_HEIGHT: int = 20_000
    AVIF_PREVIEW_MAX_INPUT_BYTES: int = 25_000_000
    AI_ANALYSIS_MAX_OUTPUT_BYTES: int = 8_000_000
    AI_ANALYSIS_MAX_WIDTH: int = 2048
    AI_ANALYSIS_MAX_HEIGHT: int = 2048
    AI_ANALYSIS_MAX_PIXELS: int = 4_194_304
    AI_ANALYSIS_MAX_DECODE_PIXELS: int = 120_000_000
    AI_ANALYSIS_JPEG_QUALITY: int = 88
    AI_ANALYSIS_MAX_VALIDATION_ATTEMPTS: int = 2
    AI_ESTIMATED_OUTPUT_UNITS: int = 4096
    AI_PILOT_CONFIRMATION_THRESHOLD_MICROS: int = 1_000_000
    AI_BATCH_MAX_ITEMS: int = 100
    AI_BATCH_MAX_REQUEST_BYTES: int = 20_000_000
    AI_ANALYSIS_BULK_MAX_ITEMS: int = 100
    AI_ANALYSIS_BULK_MAX_PAYLOAD_BYTES: int = 262_144
    AI_BATCH_MINIMUM_AGE_SECONDS: int = 300
    AI_BATCH_POLL_INTERVAL_SECONDS: float = 30.0
    AI_BATCH_MAX_ITEM_ATTEMPTS: int = 3
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: str | None = None
    GOOGLE_MANAGED_STORAGE_ACCESS_TOKEN: str | None = None
    GOOGLE_MANAGED_STORAGE_REFRESH_TOKEN: str | None = None
    GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID: str | None = None
    ELASTICSEARCH_URL: str | None = None
    ELASTICSEARCH_INDEX_PREFIX: str = "creative-assets"
    SEARCH_SUGGESTIONS_REQUEST_TIMEOUT_SECONDS: float = 0.8
    SEARCH_SUGGESTIONS_QUERY_TIMEOUT_MS: int = 300
    SEARCH_SUGGESTIONS_CACHE_TTL_SECONDS: int = 45
    SEARCH_SUGGESTIONS_CACHE_MAX_ENTRIES: int = 512
    SEARCH_SHADOW_DEFAULT_TIMEOUT_MS: int = 250
    SEARCH_SHADOW_MAX_TIMEOUT_MS: int = 2000
    SEARCH_SHADOW_DEFAULT_SAMPLE_PERCENTAGE: int = 0
    SEARCH_SHADOW_OBSERVATION_RETENTION_DAYS: int = 30
    SEARCH_SHADOW_SHUTDOWN_TIMEOUT_MS: int = 2000
    ELASTICSEARCH_INDEX_MIN_RETIREMENT_AGE_HOURS: int = 24
    ELASTICSEARCH_INDEX_MIN_PREVIOUS_VERSIONS: int = 1


    WORKER_ID: str | None = None
    WORKER_LEASE_SECONDS: int = 60
    WORKER_HEARTBEAT_SECONDS: float = 15.0
    WORKER_IDLE_POLL_SECONDS: float = 2.0
    WORKER_DRAIN_TIMEOUT_SECONDS: float = 30.0
    WORKER_HEALTH_HOST: str = "127.0.0.1"
    WORKER_HEALTH_PORT: int = 8081
    WORKER_LOG_LEVEL: str = "INFO"
    INVENTORY_WORKER_ID: str | None = None
    INVENTORY_WORKER_CONCURRENCY: int = 1
    INVENTORY_WORKER_LEASE_SECONDS: int = 60
    INVENTORY_WORKER_HEARTBEAT_SECONDS: float = 15.0
    INVENTORY_WORKER_IDLE_POLL_SECONDS: float = 2.0
    INVENTORY_WORKER_DRAIN_TIMEOUT_SECONDS: float = 30.0
    INVENTORY_WORKER_HEALTH_HOST: str = "127.0.0.1"
    INVENTORY_WORKER_HEALTH_PORT: int = 8082
    INVENTORY_SOURCE_STORAGE_ROOT: str = "/var/lib/creative-asset-manager/inventory"
    INVENTORY_DOWNLOAD_MAX_BYTES: int = 100 * 1024 * 1024
    INVENTORY_PREPARE_MAX_SOURCE_BYTES: int = 100 * 1024 * 1024
    INVENTORY_PREPARE_MAX_SOURCE_WIDTH: int = 20_000
    INVENTORY_PREPARE_MAX_SOURCE_HEIGHT: int = 20_000
    INVENTORY_PREPARE_MAX_DECODE_PIXELS: int = 120_000_000
    INVENTORY_PREPARE_MAX_OUTPUT_BYTES: int = 8 * 1024 * 1024
    INVENTORY_PREPARE_MAX_WIDTH: int = 2048
    INVENTORY_PREPARE_MAX_HEIGHT: int = 2048
    INVENTORY_PREPARE_JPEG_QUALITY: int = 85
    INVENTORY_AI_ENABLED: bool = False
    INVENTORY_AI_PROVIDER: str = "gemini"
    INVENTORY_AI_ALLOWED_MODELS: str = ""
    INVENTORY_AI_TIMEOUT_SECONDS: float = 45.0
    INVENTORY_AI_DEFAULT_DAILY_BUDGET_MICROS: int = 0
    INVENTORY_AI_DEFAULT_MONTHLY_BUDGET_MICROS: int = 0
    AI_ANALYSIS_LEASE_SECONDS: int = 300
    PROCESSING_POLICY_CACHE_TTL_SECONDS: float = 5.0
    PROCESSING_POLICY_ADMIN_IDS: str = ""
    OAUTH_TOKEN_ENCRYPTION_KEYS: str = ""
    OAUTH_ACTIVE_KEY_VERSION: str = "v1"
    SENSITIVE_URL_ENCRYPTION_KEYS: str = ""
    SENSITIVE_URL_ACTIVE_KEY_VERSION: str = "v1"
    RETENTION_INGESTION_URL_HOURS: int = 24
    RETENTION_COMPLETED_INGESTION_DAYS: int = 30
    RETENTION_RAW_AI_RESPONSE_DAYS: int = 7
    RETENTION_COMPLETED_JOB_DAYS: int = 30
    RETENTION_DEAD_LETTER_DAYS: int = 30
    RETENTION_RATE_LIMIT_HOURS: int = 2
    RETENTION_OUTBOX_DAYS: int = 30
    RETENTION_TEMP_EXPORT_DAYS: int = 7
    RETENTION_SOURCE_SYNC_RUN_DAYS: int = 30
    SOURCE_SYNC_POLL_INTERVAL_SECONDS: int = 60
    SOURCE_SYNC_MAX_SOURCES_PER_TICK: int = 100
    SOURCE_SYNC_JOB_STALE_SECONDS: int = 900
    RETENTION_CLEANUP_BATCH_SIZE: int = 500
    RETENTION_CLEANUP_MAX_ROWS: int = 5000
    RETENTION_CLEANUP_INTERVAL_SECONDS: int = 86400
    AUTH_SESSION_TTL_SECONDS: int = 30 * 24 * 60 * 60
    AUTH_STATE_TTL_SECONDS: int = 600
    AUTH_REFRESH_LEASE_SECONDS: int = 30
    AUTH_COOKIE_SECURE: bool = False
    AUTH_COOKIE_SAMESITE: str = "lax"
    AUTH_COOKIE_DOMAIN: str | None = None
    AUTH_COOKIE_PATH: str = "/"
    AUTH_DEFAULT_TENANT_ID: str = ""
    AUTH_ALLOWED_EMAIL_DOMAINS: str = ""
    AUTH_SELF_SIGNUP_DEFAULT_ROLE: str = "viewer"
    AUTH_LEGACY_ACTOR_SESSION_COMPAT_UNTIL: str = ""
    APP_ENV: str = "development"

    @field_validator(
        *FEATURE_FLAG_NAMES,
        "API_DOCS_ENABLED",
        "PROXY_HEADERS_ENABLED",
        "OPENAI_STORE_RESPONSES",
        "DEVELOPMENT_PERSONAL_TENANT_ENABLED",
        "AUTH_PROCESSING_ADMIN_ALLOWLIST_COMPAT_ENABLED",
        "AUTH_SELF_SIGNUP_ENABLED",
        mode="before",
    )
    @classmethod
    def validate_boolean_flags(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized == "true":
                return True
            if normalized == "false":
                return False
        raise ValueError("feature flags must be either 'true' or 'false'")

    @field_validator("SOURCE_SYNC_POLL_INTERVAL_SECONDS", "SOURCE_SYNC_MAX_SOURCES_PER_TICK", "SOURCE_SYNC_JOB_STALE_SECONDS")
    @classmethod
    def validate_source_sync_scheduler_limits(cls, value: int) -> int:
        if value < 1:
            raise ValueError("source sync scheduler limits must be positive")
        return value

    @property
    def inventory_tenant_allowlist(self) -> frozenset[str]:
        return frozenset(value.strip() for value in self.INVENTORY_TENANT_ALLOWLIST.split(",") if value.strip())

    @property
    def cors_allowed_origins(self) -> tuple[str, ...]:
        return tuple(
            value.strip().rstrip("/")
            for value in self.CORS_ALLOWED_ORIGINS.split(",")
            if value.strip()
        )

    @property
    def trusted_hosts(self) -> tuple[str, ...]:
        return tuple(
            value.strip() for value in self.TRUSTED_HOSTS.split(",") if value.strip()
        )

    @property
    def proxy_trusted_ips(self) -> tuple[str, ...]:
        return tuple(
            value.strip() for value in self.PROXY_TRUSTED_IPS.split(",") if value.strip()
        )

    @property
    def gemini_allowed_models(self) -> tuple[str, ...]:
        return tuple(
            value.strip()
            for value in self.GEMINI_ALLOWED_MODELS.split(",")
            if value.strip()
        )

    @property
    def gemini_model_pool(self) -> tuple[str, ...]:
        pool = tuple(dict.fromkeys(
            value.strip()
            for value in self.GEMINI_MODEL_POOL.split(",")
            if value.strip()
        ))
        return pool or (self.GEMINI_MODEL,)

    @property
    def gemini_model_limits(self) -> dict[str, GeminiModelLimit]:
        defaults = {
            "gemini-3.5-flash-lite": GeminiModelLimit(rpm=12, tpm=200000, rpd=400),
            "gemini-3.1-flash-lite": GeminiModelLimit(rpm=12, tpm=200000, rpd=400),
            "gemini-3.6-flash": GeminiModelLimit(rpm=4, tpm=200000, rpd=16),
            "gemini-2.5-flash-lite": GeminiModelLimit(rpm=8, tpm=200000, rpd=16),
            "gemini-2.5-flash": GeminiModelLimit(rpm=4, tpm=200000, rpd=16),
        }
        configured: dict[str, GeminiModelLimit] = {}
        if self.GEMINI_MODEL_LIMITS.strip():
            try:
                raw = json.loads(self.GEMINI_MODEL_LIMITS)
            except json.JSONDecodeError as exc:
                raise ValueError("GEMINI_MODEL_LIMITS must be a JSON object") from exc
            if not isinstance(raw, dict):
                raise ValueError("GEMINI_MODEL_LIMITS must be a JSON object")
            for model, limits in raw.items():
                if (
                    not isinstance(model, str)
                    or model != model.strip()
                    or any(character.isspace() for character in model)
                    or not isinstance(limits, dict)
                ):
                    raise ValueError(
                        "GEMINI_MODEL_LIMITS model keys must not contain whitespace"
                    )
                rpm, tpm, rpd = limits.get("rpm"), limits.get("tpm"), limits.get("rpd")
                if any(type(value) is not int or value < 1 for value in (rpm, tpm, rpd)):
                    raise ValueError(
                        "GEMINI_MODEL_LIMITS rpm, tpm and rpd must be positive integers"
                    )
                configured[model] = GeminiModelLimit(rpm=rpm, tpm=tpm, rpd=rpd)

        unknown_configured_models = sorted(
            set(configured) - set(self.gemini_model_pool)
        )
        if unknown_configured_models:
            raise ValueError(
                "GEMINI_MODEL_LIMITS defines models outside GEMINI_MODEL_POOL: "
                + ", ".join(unknown_configured_models)
            )

        limits_by_model: dict[str, GeminiModelLimit] = {}
        for model in self.gemini_model_pool:
            limit = configured.get(model, defaults.get(model))
            if limit is None:
                raise ValueError(
                    "GEMINI_MODEL_LIMITS must define rpm, tpm and rpd for every model in GEMINI_MODEL_POOL"
                )
            limits_by_model[model] = limit
        return limits_by_model

    @property
    def gemini_project_daily_request_limit(self) -> int:
        configured = self.GEMINI_PROJECT_DAILY_REQUEST_LIMIT
        if configured is not None:
            if configured < 1:
                raise ValueError("GEMINI_PROJECT_DAILY_REQUEST_LIMIT must be positive")
            return configured
        return sum(limit.rpd for limit in self.gemini_model_limits.values())

    @property
    def ai_model_rpm_limits(self) -> dict[tuple[str, str], int]:
        """Configured per-provider/model RPM values.

        AI_MODEL_RPM_LIMITS is JSON: {"gemini":{"gemini-2.5-flash":5},
        "openai":{"gpt-4.1-mini":3}}. Gemini model-pool defaults remain an
        while models without an explicit shared limit remain unlimited.
        """
        values: dict[tuple[str, str], int] = {}
        if self.AI_MODEL_RPM_GEMINI_2_5_FLASH is not None:
            if self.AI_MODEL_RPM_GEMINI_2_5_FLASH < 1:
                raise ValueError("AI_MODEL_RPM_GEMINI_2_5_FLASH must be positive")
            values[("gemini", "gemini-2.5-flash")] = self.AI_MODEL_RPM_GEMINI_2_5_FLASH
        if self.AI_MODEL_RPM_GPT_4_1_MINI is not None:
            if self.AI_MODEL_RPM_GPT_4_1_MINI < 1:
                raise ValueError("AI_MODEL_RPM_GPT_4_1_MINI must be positive")
            values[("openai", "gpt-4.1-mini")] = self.AI_MODEL_RPM_GPT_4_1_MINI
        if not self.AI_MODEL_RPM_LIMITS.strip():
            return values
        try:
            raw = json.loads(self.AI_MODEL_RPM_LIMITS)
        except json.JSONDecodeError as exc:
            raise ValueError("AI_MODEL_RPM_LIMITS must be a JSON object") from exc
        if not isinstance(raw, dict):
            raise ValueError("AI_MODEL_RPM_LIMITS must be a JSON object")
        for provider, models in raw.items():
            if not isinstance(provider, str) or not isinstance(models, dict):
                raise ValueError("AI_MODEL_RPM_LIMITS must contain provider model objects")
            for model, rpm in models.items():
                if (
                    not isinstance(model, str)
                    or model != model.strip()
                    or any(character.isspace() for character in model)
                    or type(rpm) is not int
                    or rpm < 1
                ):
                    raise ValueError(
                        "AI_MODEL_RPM_LIMITS model keys must be whitespace-free positive integer entries"
                    )
                normalized_provider = provider.strip().lower()
                if normalized_provider == "gemini" and model not in self.gemini_model_pool:
                    raise ValueError(
                        "AI_MODEL_RPM_LIMITS Gemini models must exist in GEMINI_MODEL_POOL: "
                        + model
                    )
                values[(normalized_provider, model)] = rpm
        return values

    def ai_model_rpm(self, provider: str, model: str) -> int | None:
        return self.ai_model_rpm_limits.get((provider.strip().lower(), model.strip()))

    @property
    def openai_allowed_models(self) -> tuple[str, ...]:
        return tuple(
            value.strip()
            for value in self.OPENAI_ALLOWED_MODELS.split(",")
            if value.strip()
        )

    @property
    def elasticsearch_readiness_required(self) -> bool:
        return any(
            (
                self.ELASTICSEARCH_V2_ENABLED,
                self.SEARCH_V3_ENABLED,
                self.ELASTICSEARCH_INDEX_LIFECYCLE_ENABLED,
                self.SEARCH_SHADOW_COMPARISON_ENABLED,
            )
        )

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.strip().lower() in {"production", "prod"}

    @property
    def auth_allowed_email_domains(self) -> tuple[str, ...]:
        return tuple(
            value.strip().casefold()
            for value in self.AUTH_ALLOWED_EMAIL_DOMAINS.split(",")
            if value.strip()
        )

    @property
    def legacy_actor_session_compatibility_enabled(self) -> bool:
        value = self.AUTH_LEGACY_ACTOR_SESSION_COMPAT_UNTIL.strip()
        if not value:
            return False
        deadline = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) < deadline

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_database_url(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator(
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_ORGANIZATION",
        "OPENAI_PROJECT",
        mode="before",
    )
    @classmethod
    def normalize_optional_openai_setting(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("OPENAI_DEFAULT_MODEL", "OPENAI_IMAGE_DETAIL")
    @classmethod
    def normalize_openai_setting(cls, value: str) -> str:
        return value.strip()

    @field_validator("APP_VERSION", "BUILD_COMMIT")
    @classmethod
    def validate_build_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not _BUILD_IDENTIFIER_RE.fullmatch(normalized):
            raise ValueError("Build identifiers contain invalid characters")
        return normalized

    @field_validator("AUTH_COOKIE_SAMESITE")
    @classmethod
    def validate_cookie_samesite(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"lax", "strict", "none"}:
            raise ValueError("AUTH_COOKIE_SAMESITE must be lax, strict or none")
        return normalized

    @field_validator("AUTH_SELF_SIGNUP_DEFAULT_ROLE")
    @classmethod
    def validate_self_signup_default_role(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not _ROLE_KEY_RE.fullmatch(normalized):
            raise ValueError("AUTH_SELF_SIGNUP_DEFAULT_ROLE is invalid")
        if normalized in {"tenant_admin", "platform_admin"}:
            raise ValueError(
                "AUTH_SELF_SIGNUP_DEFAULT_ROLE cannot grant administration"
            )
        return normalized

    @field_validator("WORKER_LOG_LEVEL")
    @classmethod
    def validate_worker_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("WORKER_LOG_LEVEL is invalid")
        return normalized

    @model_validator(mode="after")
    def validate_worker_runtime(self) -> "Settings":
        public_url = urlsplit(self.PUBLIC_APP_URL)
        if (
            public_url.scheme not in {"http", "https"}
            or not public_url.hostname
            or public_url.username
            or public_url.password
            or public_url.query
            or public_url.fragment
        ):
            raise ValueError(
                "PUBLIC_APP_URL must be an absolute HTTP(S) URL without credentials"
            )
        origins = self.cors_allowed_origins
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                "*" in origin
                or parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "CORS_ALLOWED_ORIGINS must contain comma-separated HTTP(S) origins"
                )
        proxy_ips = self.proxy_trusted_ips
        if self.PROXY_HEADERS_ENABLED and not proxy_ips:
            raise ValueError("PROXY_TRUSTED_IPS is required when proxy headers are enabled")
        for value in proxy_ips:
            if value == "*":
                raise ValueError("Wildcard proxy trust is not allowed")
            try:
                network = ipaddress.ip_network(value, strict=False)
            except ValueError as exc:
                raise ValueError("PROXY_TRUSTED_IPS must contain IPs or CIDRs") from exc
            if network.prefixlen == 0:
                raise ValueError("Trusting every proxy address is not allowed")
        if not 0 < self.HEALTHCHECK_TIMEOUT_SECONDS <= 30:
            raise ValueError("HEALTHCHECK_TIMEOUT_SECONDS must be between 0 and 30")
        hosts = self.trusted_hosts
        if not hosts:
            raise ValueError("TRUSTED_HOSTS must contain at least one hostname")
        if any(
            "://" in host
            or "/" in host
            or ":" in host
            or any(char.isspace() for char in host)
            for host in hosts
        ):
            raise ValueError("TRUSTED_HOSTS must contain hostnames, not URLs")
        if self.is_production:
            if (
                public_url.scheme != "https"
                or public_url.hostname in {"localhost", "127.0.0.1", "::1"}
            ):
                raise ValueError(
                    "PUBLIC_APP_URL must use a non-local HTTPS URL in production"
                )
            if self.API_DOCS_ENABLED:
                raise ValueError("API_DOCS_ENABLED must be false in production")
            if not hosts or any(
                host == "*" or "localhost" in host or host.startswith("127.")
                for host in hosts
            ):
                raise ValueError(
                    "TRUSTED_HOSTS must be explicit non-local hosts in production"
                )
            if origins:
                public_origin = (
                    f"{public_url.scheme}://{public_url.netloc}".rstrip("/")
                )
                if public_origin not in origins:
                    raise ValueError(
                        "CORS_ALLOWED_ORIGINS must include PUBLIC_APP_URL origin "
                        "in production"
                    )
                if any(
                    urlsplit(origin).scheme != "https"
                    or urlsplit(origin).hostname
                    in {"localhost", "127.0.0.1", "::1"}
                    for origin in origins
                ):
                    raise ValueError(
                        "Production CORS origins must use non-local HTTPS URLs"
                    )
            if not self.DATABASE_URL:
                raise ValueError("DATABASE_URL is required in production")
            if self.DATABASE_URL.lower().startswith("sqlite"):
                raise ValueError("SQLite is not supported in production")
            if self.DEVELOPMENT_PERSONAL_TENANT_ENABLED:
                raise ValueError("DEVELOPMENT_PERSONAL_TENANT_ENABLED is forbidden in production")
            if (
                self.AUTH_PROCESSING_ADMIN_ALLOWLIST_COMPAT_ENABLED
                or self.PROCESSING_POLICY_ADMIN_IDS.strip()
            ):
                raise ValueError("legacy processing admin allowlist is forbidden in production")
            if self.AUTH_SELF_SIGNUP_ENABLED and not self.AUTH_DEFAULT_TENANT_ID.strip():
                raise ValueError("AUTH_DEFAULT_TENANT_ID is required for production self-signup")
        domains = self.auth_allowed_email_domains
        if any(
            "@" in domain
            or "." not in domain
            or any(character.isspace() for character in domain)
            for domain in domains
        ):
            raise ValueError("AUTH_ALLOWED_EMAIL_DOMAINS contains an invalid domain")
        legacy_deadline = self.AUTH_LEGACY_ACTOR_SESSION_COMPAT_UNTIL.strip()
        if legacy_deadline:
            try:
                parsed_deadline = datetime.fromisoformat(legacy_deadline.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("AUTH_LEGACY_ACTOR_SESSION_COMPAT_UNTIL must be ISO-8601") from exc
            if parsed_deadline.tzinfo is None:
                raise ValueError("AUTH_LEGACY_ACTOR_SESSION_COMPAT_UNTIL must include a timezone")
        if min(
            self.DATABASE_POOL_SIZE,
            self.DATABASE_POOL_TIMEOUT_SECONDS,
            self.DATABASE_POOL_RECYCLE_SECONDS,
            self.DATABASE_CONNECT_TIMEOUT_SECONDS,
        ) <= 0:
            raise ValueError("Database pool and timeout settings must be positive")
        if self.DATABASE_MAX_OVERFLOW < 0:
            raise ValueError("DATABASE_MAX_OVERFLOW cannot be negative")
        if self.PERSISTENT_AUTH_ENABLED:
            import base64
            versions = set()
            for item in self.OAUTH_TOKEN_ENCRYPTION_KEYS.split(","):
                if not item.strip() or ":" not in item:
                    raise ValueError("OAUTH_TOKEN_ENCRYPTION_KEYS must contain versioned keys")
                version, encoded = item.strip().split(":", 1)
                try:
                    decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
                except Exception as exc:
                    raise ValueError("OAUTH_TOKEN_ENCRYPTION_KEYS is invalid") from exc
                if not version or version in versions or len(decoded) != 32:
                    raise ValueError("OAuth keys require unique versions and 32-byte values")
                versions.add(version)
            if self.OAUTH_ACTIVE_KEY_VERSION not in versions:
                raise ValueError("OAUTH_ACTIVE_KEY_VERSION is unavailable")
            if self.APP_ENV.lower() in {"production", "prod"} and not self.AUTH_COOKIE_SECURE:
                raise ValueError("AUTH_COOKIE_SECURE must be true in production")
            if self.AUTH_COOKIE_SAMESITE == "none" and not self.AUTH_COOKIE_SECURE:
                raise ValueError("SameSite=None requires secure cookies")
        if self.EXTERNAL_INGESTION_API_ENABLED:
            import base64
            versions = set()
            for item in self.SENSITIVE_URL_ENCRYPTION_KEYS.split(","):
                if not item.strip() or ":" not in item:
                    raise ValueError("SENSITIVE_URL_ENCRYPTION_KEYS must contain versioned keys")
                version, encoded = item.strip().split(":", 1)
                try:
                    decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
                except Exception as exc:
                    raise ValueError("SENSITIVE_URL_ENCRYPTION_KEYS is invalid") from exc
                if not version or version in versions or len(decoded) != 32:
                    raise ValueError("Sensitive URL keys require unique versions and 32-byte values")
                versions.add(version)
            if self.SENSITIVE_URL_ACTIVE_KEY_VERSION not in versions:
                raise ValueError("SENSITIVE_URL_ACTIVE_KEY_VERSION is unavailable")
        retention_values = (
            self.RETENTION_INGESTION_URL_HOURS,
            self.RETENTION_COMPLETED_INGESTION_DAYS,
            self.RETENTION_RAW_AI_RESPONSE_DAYS,
            self.RETENTION_COMPLETED_JOB_DAYS,
            self.RETENTION_DEAD_LETTER_DAYS,
            self.RETENTION_RATE_LIMIT_HOURS,
            self.RETENTION_OUTBOX_DAYS,
            self.RETENTION_TEMP_EXPORT_DAYS,
            self.RETENTION_SOURCE_SYNC_RUN_DAYS,
            self.RETENTION_CLEANUP_BATCH_SIZE,
            self.RETENTION_CLEANUP_MAX_ROWS,
            self.RETENTION_CLEANUP_INTERVAL_SECONDS,
        )
        if min(retention_values) <= 0:
            raise ValueError("Retention settings must be positive")
        if self.RETENTION_CLEANUP_BATCH_SIZE > self.RETENTION_CLEANUP_MAX_ROWS:
            raise ValueError("RETENTION_CLEANUP_BATCH_SIZE cannot exceed RETENTION_CLEANUP_MAX_ROWS")
        if not 0 < self.SEARCH_SUGGESTIONS_REQUEST_TIMEOUT_SECONDS <= 5:
            raise ValueError("SEARCH_SUGGESTIONS_REQUEST_TIMEOUT_SECONDS must be between 0 and 5")
        if not 0 < self.SEARCH_SUGGESTIONS_QUERY_TIMEOUT_MS <= 5000:
            raise ValueError("SEARCH_SUGGESTIONS_QUERY_TIMEOUT_MS must be between 0 and 5000")
        if self.SEARCH_SUGGESTIONS_CACHE_TTL_SECONDS < 1:
            raise ValueError("SEARCH_SUGGESTIONS_CACHE_TTL_SECONDS must be positive")
        if self.SEARCH_SUGGESTIONS_CACHE_MAX_ENTRIES < 1:
            raise ValueError("SEARCH_SUGGESTIONS_CACHE_MAX_ENTRIES must be positive")
        if not 0 <= self.SEARCH_SHADOW_DEFAULT_SAMPLE_PERCENTAGE <= 100:
            raise ValueError("SEARCH_SHADOW_DEFAULT_SAMPLE_PERCENTAGE must be between 0 and 100")
        if not 0 < self.SEARCH_SHADOW_DEFAULT_TIMEOUT_MS <= self.SEARCH_SHADOW_MAX_TIMEOUT_MS:
            raise ValueError("Search shadow timeout must be positive and within its maximum")
        if self.SEARCH_SHADOW_SHUTDOWN_TIMEOUT_MS <= 0:
            raise ValueError("Search shadow shutdown timeout must be positive")
        if min(
            self.SEARCH_SHADOW_OBSERVATION_RETENTION_DAYS,
            self.ELASTICSEARCH_INDEX_MIN_RETIREMENT_AGE_HOURS,
            self.ELASTICSEARCH_INDEX_MIN_PREVIOUS_VERSIONS,
        ) <= 0:
            raise ValueError("Search shadow retention and index lifecycle settings must be positive")
        if min(self.AUTH_SESSION_TTL_SECONDS, self.AUTH_STATE_TTL_SECONDS, self.AUTH_REFRESH_LEASE_SECONDS) <= 0:
            raise ValueError("Authentication TTL and lease settings must be positive")
        if not self.AUTH_COOKIE_PATH.startswith("/"):
            raise ValueError("AUTH_COOKIE_PATH must be absolute")
        if self.WORKER_LEASE_SECONDS <= 0:
            raise ValueError("WORKER_LEASE_SECONDS must be positive")
        if not 0 < self.WORKER_HEARTBEAT_SECONDS < self.WORKER_LEASE_SECONDS:
            raise ValueError(
                "WORKER_HEARTBEAT_SECONDS must be positive and shorter than the lease"
            )
        if self.WORKER_IDLE_POLL_SECONDS <= 0:
            raise ValueError("WORKER_IDLE_POLL_SECONDS must be positive")
        if self.WORKER_DRAIN_TIMEOUT_SECONDS < 0:
            raise ValueError("WORKER_DRAIN_TIMEOUT_SECONDS cannot be negative")
        if not 1 <= self.WORKER_HEALTH_PORT <= 65535:
            raise ValueError("WORKER_HEALTH_PORT must be between 1 and 65535")
        if self.INVENTORY_WORKER_ENABLED and not self.INVENTORY_AUTOMATION_ENABLED:
            raise ValueError(
                "INVENTORY_AUTOMATION_ENABLED is required when the Inventory worker is enabled"
            )
        if self.INVENTORY_DRIVE_POLLER_ENABLED and not (
            self.INVENTORY_AUTOMATION_ENABLED and self.INVENTORY_WORKER_ENABLED
        ):
            raise ValueError(
                "Inventory automation and worker flags are required when the Drive poller is enabled"
            )
        if self.INVENTORY_DAILY_SCHEDULER_ENABLED and not self.INVENTORY_AUTOMATION_ENABLED:
            raise ValueError("INVENTORY_AUTOMATION_ENABLED is required when the Inventory daily scheduler is enabled")
        inventory_runtime_enabled = any((
            self.INVENTORY_AUTOMATION_ENABLED,
            self.INVENTORY_WORKER_ENABLED,
            self.INVENTORY_DRIVE_POLLER_ENABLED,
            self.INVENTORY_DAILY_SCHEDULER_ENABLED,
            self.INVENTORY_AI_ENABLED,
        ))
        if inventory_runtime_enabled and not self.inventory_tenant_allowlist:
            raise ValueError("INVENTORY_TENANT_ALLOWLIST is required whenever Inventory runtime is enabled")
        if self.INVENTORY_AI_ENABLED and not self.INVENTORY_AI_GEMINI_API_KEY:
            raise ValueError("INVENTORY_AI_GEMINI_API_KEY is required when Inventory AI is enabled")
        if not self.INVENTORY_AI_PROJECT_QUOTA_SCOPE.strip():
            raise ValueError("INVENTORY_AI_PROJECT_QUOTA_SCOPE is required")
        if self.INVENTORY_DAILY_SCHEDULER_POLL_SECONDS <= 0:
            raise ValueError("INVENTORY_DAILY_SCHEDULER_POLL_SECONDS must be positive")
        if self.INVENTORY_DOWNLOAD_MAX_BYTES <= 0:
            raise ValueError("INVENTORY_DOWNLOAD_MAX_BYTES must be positive")
        if self.INVENTORY_PREPARE_MAX_SOURCE_BYTES <= 0:
            raise ValueError("INVENTORY_PREPARE_MAX_SOURCE_BYTES must be positive")
        if min(self.INVENTORY_PREPARE_MAX_SOURCE_WIDTH, self.INVENTORY_PREPARE_MAX_SOURCE_HEIGHT) <= 0:
            raise ValueError("Inventory preparation source dimensions must be positive")
        if self.INVENTORY_PREPARE_MAX_DECODE_PIXELS <= 0:
            raise ValueError("INVENTORY_PREPARE_MAX_DECODE_PIXELS must be positive")
        if self.INVENTORY_PREPARE_MAX_OUTPUT_BYTES <= 0:
            raise ValueError("INVENTORY_PREPARE_MAX_OUTPUT_BYTES must be positive")
        if min(self.INVENTORY_PREPARE_MAX_WIDTH, self.INVENTORY_PREPARE_MAX_HEIGHT) <= 0:
            raise ValueError("Inventory preparation output dimensions must be positive")
        if not 1 <= self.INVENTORY_PREPARE_JPEG_QUALITY <= 95:
            raise ValueError("INVENTORY_PREPARE_JPEG_QUALITY must be between 1 and 95")
        if not self.INVENTORY_SOURCE_STORAGE_ROOT.strip():
            raise ValueError("INVENTORY_SOURCE_STORAGE_ROOT is required")
        if self.INVENTORY_WORKER_CONCURRENCY <= 0:
            raise ValueError("INVENTORY_WORKER_CONCURRENCY must be positive")
        if self.INVENTORY_WORKER_LEASE_SECONDS <= 0:
            raise ValueError("INVENTORY_WORKER_LEASE_SECONDS must be positive")
        if not (
            0
            < self.INVENTORY_WORKER_HEARTBEAT_SECONDS
            < self.INVENTORY_WORKER_LEASE_SECONDS
        ):
            raise ValueError(
                "INVENTORY_WORKER_HEARTBEAT_SECONDS must be positive and shorter than the lease"
            )
        if self.INVENTORY_WORKER_IDLE_POLL_SECONDS <= 0:
            raise ValueError("INVENTORY_WORKER_IDLE_POLL_SECONDS must be positive")
        if self.INVENTORY_WORKER_DRAIN_TIMEOUT_SECONDS < 0:
            raise ValueError("INVENTORY_WORKER_DRAIN_TIMEOUT_SECONDS cannot be negative")
        if not 1 <= self.INVENTORY_WORKER_HEALTH_PORT <= 65535:
            raise ValueError("INVENTORY_WORKER_HEALTH_PORT must be between 1 and 65535")
        if self.GEMINI_TIMEOUT_SECONDS <= 0:
            raise ValueError("GEMINI_TIMEOUT_SECONDS must be positive")
        if self.GEMINI_MODEL_COOLDOWN_SECONDS < 0:
            raise ValueError("GEMINI_MODEL_COOLDOWN_SECONDS cannot be negative")
        if self.AI_JOB_MIN_INTERVAL_SECONDS < 10:
            raise ValueError("AI_JOB_MIN_INTERVAL_SECONDS must be at least 10 seconds")
        if self.AI_JOB_RATE_LIMIT_SAFETY_SECONDS < 0:
            raise ValueError("AI_JOB_RATE_LIMIT_SAFETY_SECONDS cannot be negative")
        if self.AI_RATE_LIMIT_429_MAX_RETRIES < 0:
            raise ValueError("AI_RATE_LIMIT_429_MAX_RETRIES cannot be negative")
        if self.AI_RATE_LIMIT_BACKOFF_MAX_SECONDS < 10:
            raise ValueError("AI_RATE_LIMIT_BACKOFF_MAX_SECONDS must be at least 10 seconds")
        # Parse once during startup so malformed per-model limits fail closed.
        _ = self.ai_model_rpm_limits
        if self.OPENAI_TIMEOUT_SECONDS <= 0:
            raise ValueError("OPENAI_TIMEOUT_SECONDS must be positive")
        if self.OPENAI_MAX_RETRIES < 0:
            raise ValueError("OPENAI_MAX_RETRIES cannot be negative")
        if self.OPENAI_IMAGE_DETAIL not in {"auto", "low", "high", "original"}:
            raise ValueError("OPENAI_IMAGE_DETAIL is invalid")
        if self.OPENAI_BATCH_COMPLETION_WINDOW != "24h":
            raise ValueError("OPENAI_BATCH_COMPLETION_WINDOW must be 24h")
        if min(
            self.OPENAI_BATCH_MAX_ITEMS,
            self.OPENAI_BATCH_MAX_FILE_BYTES,
            self.OPENAI_BATCH_RESULT_PAGE_OR_CHUNK_SIZE,
            self.OPENAI_BATCH_INPUT_RETENTION_HOURS,
            self.OPENAI_BATCH_OUTPUT_RETENTION_HOURS,
        ) <= 0:
            raise ValueError("OpenAI batch limits and retention must be positive")
        if self.OPENAI_BATCH_POLL_INTERVAL_SECONDS <= 0:
            raise ValueError("OPENAI_BATCH_POLL_INTERVAL_SECONDS must be positive")
        if (
            self.OPENAI_BATCH_INPUT_RETENTION_HOURS > 720
            or self.OPENAI_BATCH_OUTPUT_RETENTION_HOURS > 720
        ):
            raise ValueError("OpenAI batch retention cannot exceed 720 hours")
        if not 0 < self.PROCESSING_POLICY_CACHE_TTL_SECONDS <= 60:
            raise ValueError("PROCESSING_POLICY_CACHE_TTL_SECONDS must be between 0 and 60")
        if self.AI_ANALYSIS_LEASE_SECONDS <= 0:
            raise ValueError("AI_ANALYSIS_LEASE_SECONDS must be positive")
        if min(
            self.AI_ANALYSIS_MAX_SOURCE_BYTES,
            self.AI_ANALYSIS_MAX_SOURCE_WIDTH,
            self.AI_ANALYSIS_MAX_SOURCE_HEIGHT,
            self.AVIF_PREVIEW_MAX_INPUT_BYTES,
            self.AI_ANALYSIS_MAX_OUTPUT_BYTES,
            self.AI_ANALYSIS_MAX_WIDTH,
            self.AI_ANALYSIS_MAX_HEIGHT,
            self.AI_ANALYSIS_MAX_PIXELS,
            self.AI_ANALYSIS_MAX_DECODE_PIXELS,
            self.AI_ANALYSIS_MAX_VALIDATION_ATTEMPTS,
            self.AI_ESTIMATED_OUTPUT_UNITS,
            self.AI_BATCH_MAX_ITEMS,
            self.AI_BATCH_MAX_REQUEST_BYTES,
            self.AI_ANALYSIS_BULK_MAX_ITEMS,
            self.AI_ANALYSIS_BULK_MAX_PAYLOAD_BYTES,
            self.AI_BATCH_MAX_ITEM_ATTEMPTS,
        ) <= 0:
            raise ValueError("AI analysis limits must be positive")
        if self.AI_ANALYSIS_BULK_MAX_ITEMS > 1_000:
            raise ValueError("AI_ANALYSIS_BULK_MAX_ITEMS cannot exceed 1000")
        if self.AI_BATCH_MINIMUM_AGE_SECONDS < 0:
            raise ValueError("AI_BATCH_MINIMUM_AGE_SECONDS cannot be negative")
        if self.AI_BATCH_POLL_INTERVAL_SECONDS <= 0:
            raise ValueError("AI_BATCH_POLL_INTERVAL_SECONDS must be positive")
        if self.AI_PILOT_CONFIRMATION_THRESHOLD_MICROS < 0:
            raise ValueError("AI_PILOT_CONFIRMATION_THRESHOLD_MICROS cannot be negative")
        if not 1 <= self.AI_ANALYSIS_JPEG_QUALITY <= 95:
            raise ValueError("AI_ANALYSIS_JPEG_QUALITY must be between 1 and 95")
        if self.GEMINI_MODEL not in self.gemini_allowed_models:
            raise ValueError(
                "GEMINI_MODEL must be in GEMINI_ALLOWED_MODELS"
            )
        self.gemini_model_limits
        if self.OPENAI_BASE_URL:
            openai_url = urlsplit(self.OPENAI_BASE_URL)
            if (
                openai_url.scheme not in {"http", "https"}
                or not openai_url.hostname
                or openai_url.username
                or openai_url.password
                or openai_url.query
                or openai_url.fragment
            ):
                raise ValueError("OPENAI_BASE_URL must be an absolute HTTP(S) URL")
            if self.is_production and openai_url.scheme != "https":
                raise ValueError("OPENAI_BASE_URL must use HTTPS in production")
        if self.OPENAI_AI_ENABLED:
            if not self.OPENAI_API_KEY:
                raise ValueError(
                    "OPENAI_API_KEY is required when OpenAI AI is enabled"
                )
            if not self.OPENAI_DEFAULT_MODEL:
                raise ValueError(
                    "OPENAI_DEFAULT_MODEL is required when OpenAI AI is enabled"
                )
            if self.OPENAI_DEFAULT_MODEL not in self.openai_allowed_models:
                raise ValueError(
                    "OPENAI_DEFAULT_MODEL must be in OPENAI_ALLOWED_MODELS"
                )
        if (
            self.DYNAMIC_AI_METADATA_ENABLED
            and self.AI_SINGLE_ANALYSIS_ENABLED
            and not (
                self.GEMINI_API_KEY
                or (self.OPENAI_AI_ENABLED and self.OPENAI_API_KEY)
            )
        ):
            raise ValueError(
                "At least one configured AI provider is required for single analysis"
            )
        if (
            self.DYNAMIC_AI_METADATA_ENABLED
            and self.AI_BATCH_ANALYSIS_ENABLED
            and not (
                self.GEMINI_API_KEY
                or (self.OPENAI_AI_ENABLED and self.OPENAI_BATCH_ENABLED
                    and self.OPENAI_API_KEY)
            )
        ):
            raise ValueError(
                "A configured batch-capable AI provider is required for batch analysis"
            )
        return self

@lru_cache
def get_settings() -> Settings:
    return Settings()