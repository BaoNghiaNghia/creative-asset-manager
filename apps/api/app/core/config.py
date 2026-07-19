from functools import lru_cache
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @field_validator("*", mode="before")
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
