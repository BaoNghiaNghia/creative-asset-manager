from functools import lru_cache
from typing import Any

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
)


class Settings(BaseSettings):
    """Validated application settings for architecture rollout flags."""

    model_config = SettingsConfigDict(extra="ignore")

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

    @field_validator(*FEATURE_FLAG_NAMES, mode="before")
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

    @field_validator("WORKER_LOG_LEVEL")
    @classmethod
    def validate_worker_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("WORKER_LOG_LEVEL is invalid")
        return normalized

    @model_validator(mode="after")
    def validate_worker_runtime(self) -> "Settings":
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
