from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

FEATURE_FLAG_NAMES = (
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
    "AI_EMERGENCY_STOP_ENABLED",
    "AI_BATCH_FALLBACK_TO_SINGLE_ENABLED",
    "PERSISTENT_AUTH_ENABLED",
    "RETENTION_CLEANUP_ENABLED",
    "DETERMINISTIC_ACTIVE_ANALYSIS_ENABLED",
    "SEARCH_SHADOW_COMPARISON_ENABLED",
    "ELASTICSEARCH_INDEX_LIFECYCLE_ENABLED",
)


class Settings(BaseSettings):
    """Validated application settings for architecture rollout flags."""

    model_config = SettingsConfigDict(extra="ignore")

    PUBLIC_APP_URL: str = "http://localhost:5173"
    CORS_ALLOWED_ORIGINS: str = "http://localhost:5173"
    TRUSTED_HOSTS: str = "localhost,127.0.0.1,testserver"
    API_DOCS_ENABLED: bool = True
    DATABASE_URL: str | None = None
    DATABASE_POOL_SIZE: int = 5
    DATABASE_MAX_OVERFLOW: int = 10
    DATABASE_POOL_TIMEOUT_SECONDS: float = 30.0
    DATABASE_POOL_RECYCLE_SECONDS: int = 1800
    DATABASE_CONNECT_TIMEOUT_SECONDS: int = 10

    UNIFIED_ASSET_INGESTION_ENABLED: bool = False
    CONTENT_DEDUP_ENABLED: bool = False
    INCREMENTAL_SOURCE_SYNC_ENABLED: bool = False
    PROCESSING_JOBS_ENABLED: bool = False
    EXTERNAL_ASSET_DOWNLOADER_ENABLED: bool = False
    MANAGED_ASSET_STORAGE_ENABLED: bool = False
    DYNAMIC_AI_METADATA_ENABLED: bool = False
    AI_SINGLE_ANALYSIS_ENABLED: bool = False
    AI_BATCH_ANALYSIS_ENABLED: bool = False
    AI_AUTO_ANALYZE_ENABLED: bool = False
    SEARCH_PROJECTION_ENABLED: bool = False
    ELASTICSEARCH_V2_ENABLED: bool = False
    SEARCH_QUERY_PARSER_V2_ENABLED: bool = False
    EXTERNAL_INGESTION_API_ENABLED: bool = False
    DRIVE_METADATA_SIDECAR_ENABLED: bool = False
    AI_EMERGENCY_STOP_ENABLED: bool = False
    AI_BATCH_FALLBACK_TO_SINGLE_ENABLED: bool = False
    PERSISTENT_AUTH_ENABLED: bool = False
    RETENTION_CLEANUP_ENABLED: bool = False
    DETERMINISTIC_ACTIVE_ANALYSIS_ENABLED: bool = False
    SEARCH_SHADOW_COMPARISON_ENABLED: bool = False
    ELASTICSEARCH_INDEX_LIFECYCLE_ENABLED: bool = False
    AI_STORE_RAW_RESPONSE_ENABLED: bool = False
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_TIMEOUT_SECONDS: float = 45.0
    AI_ANALYSIS_MAX_SOURCE_BYTES: int = 25_000_000
    AI_ANALYSIS_MAX_OUTPUT_BYTES: int = 8_000_000
    AI_ANALYSIS_MAX_WIDTH: int = 4096
    AI_ANALYSIS_MAX_HEIGHT: int = 4096
    AI_ANALYSIS_MAX_PIXELS: int = 24_000_000
    AI_ANALYSIS_JPEG_QUALITY: int = 88
    AI_ANALYSIS_MAX_VALIDATION_ATTEMPTS: int = 2
    AI_ESTIMATED_OUTPUT_UNITS: int = 4096
    AI_PILOT_CONFIRMATION_THRESHOLD_MICROS: int = 1_000_000
    AI_BATCH_MAX_ITEMS: int = 100
    AI_BATCH_MAX_REQUEST_BYTES: int = 20_000_000
    AI_BATCH_MINIMUM_AGE_SECONDS: int = 300
    AI_BATCH_POLL_INTERVAL_SECONDS: float = 30.0
    AI_BATCH_MAX_ITEM_ATTEMPTS: int = 3
    GOOGLE_MANAGED_STORAGE_ACCESS_TOKEN: str | None = None
    GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID: str | None = None
    ELASTICSEARCH_URL: str | None = None
    ELASTICSEARCH_INDEX_PREFIX: str = "creative-assets"
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
    APP_ENV: str = "development"

    @field_validator(*FEATURE_FLAG_NAMES, "API_DOCS_ENABLED", mode="before")
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
    def is_production(self) -> bool:
        return self.APP_ENV.strip().lower() in {"production", "prod"}

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_database_url(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("AUTH_COOKIE_SAMESITE")
    @classmethod
    def validate_cookie_samesite(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"lax", "strict", "none"}:
            raise ValueError("AUTH_COOKIE_SAMESITE must be lax, strict or none")
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
        if self.GEMINI_TIMEOUT_SECONDS <= 0:
            raise ValueError("GEMINI_TIMEOUT_SECONDS must be positive")
        if not 0 < self.PROCESSING_POLICY_CACHE_TTL_SECONDS <= 60:
            raise ValueError("PROCESSING_POLICY_CACHE_TTL_SECONDS must be between 0 and 60")
        if self.AI_ANALYSIS_LEASE_SECONDS <= 0:
            raise ValueError("AI_ANALYSIS_LEASE_SECONDS must be positive")
        if min(
            self.AI_ANALYSIS_MAX_SOURCE_BYTES,
            self.AI_ANALYSIS_MAX_OUTPUT_BYTES,
            self.AI_ANALYSIS_MAX_WIDTH,
            self.AI_ANALYSIS_MAX_HEIGHT,
            self.AI_ANALYSIS_MAX_PIXELS,
            self.AI_ANALYSIS_MAX_VALIDATION_ATTEMPTS,
            self.AI_ESTIMATED_OUTPUT_UNITS,
            self.AI_BATCH_MAX_ITEMS,
            self.AI_BATCH_MAX_REQUEST_BYTES,
            self.AI_BATCH_MAX_ITEM_ATTEMPTS,
        ) <= 0:
            raise ValueError("AI analysis limits must be positive")
        if self.AI_BATCH_MINIMUM_AGE_SECONDS < 0:
            raise ValueError("AI_BATCH_MINIMUM_AGE_SECONDS cannot be negative")
        if self.AI_BATCH_POLL_INTERVAL_SECONDS <= 0:
            raise ValueError("AI_BATCH_POLL_INTERVAL_SECONDS must be positive")
        if self.AI_PILOT_CONFIRMATION_THRESHOLD_MICROS < 0:
            raise ValueError("AI_PILOT_CONFIRMATION_THRESHOLD_MICROS cannot be negative")
        if not 1 <= self.AI_ANALYSIS_JPEG_QUALITY <= 95:
            raise ValueError("AI_ANALYSIS_JPEG_QUALITY must be between 1 and 95")
        if (
            self.DYNAMIC_AI_METADATA_ENABLED
            and (self.AI_SINGLE_ANALYSIS_ENABLED or self.AI_BATCH_ANALYSIS_ENABLED)
            and not self.GEMINI_API_KEY
        ):
            raise ValueError("GEMINI_API_KEY is required when AI analysis is enabled")
        return self

@lru_cache
def get_settings() -> Settings:
    return Settings()
