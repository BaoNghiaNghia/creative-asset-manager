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

    WORKER_ID: str | None = None
    WORKER_LEASE_SECONDS: int = 60
    WORKER_HEARTBEAT_SECONDS: float = 15.0
    WORKER_IDLE_POLL_SECONDS: float = 2.0
    WORKER_DRAIN_TIMEOUT_SECONDS: float = 30.0
    WORKER_HEALTH_HOST: str = "127.0.0.1"
    WORKER_HEALTH_PORT: int = 8081
    WORKER_LOG_LEVEL: str = "INFO"

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
        return self

@lru_cache
def get_settings() -> Settings:
    return Settings()
