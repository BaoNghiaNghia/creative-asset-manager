from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Mutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=1_000)


class AiPauseRequest(_Mutation):
    pass


class AiDefaultsUpdate(_Mutation):
    provider: Literal["gemini", "openai"]
    model: str = Field(min_length=1, max_length=255)


class AiProviderControlUpdate(_Mutation):
    processing_enabled: bool | None = None
    single_enabled: bool | None = None
    batch_enabled: bool | None = None
    active_jobs_limit: int | None = Field(default=None, ge=1, le=100)
    single_active_jobs_limit: int | None = Field(default=None, ge=1, le=100)
    batch_active_jobs_limit: int | None = Field(default=None, ge=1, le=100)
    tenant_ai_active_jobs_limit: int | None = Field(default=None, ge=1, le=500)

    @model_validator(mode="after")
    def require_change(self) -> "AiProviderControlUpdate":
        if all(
            getattr(self, name) is None
            for name in (
                "processing_enabled", "single_enabled", "batch_enabled", "active_jobs_limit",
                "single_active_jobs_limit", "batch_active_jobs_limit",
                "tenant_ai_active_jobs_limit",
            )
        ):
            raise ValueError("At least one control field is required")
        return self


class AiBudgetUpdate(_Mutation):
    enabled: bool = True
    daily_limit_micros: int | None = Field(default=None, ge=0)
    monthly_limit_micros: int | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    warning_threshold_percent: int = Field(default=80, ge=0, le=100)
    hard_stop_threshold_percent: int = Field(default=100, ge=1, le=100)

    @model_validator(mode="after")
    def validate_limits(self) -> "AiBudgetUpdate":
        if self.enabled and self.daily_limit_micros is None and self.monthly_limit_micros is None:
            raise ValueError("An enabled budget requires a daily or monthly limit")
        if self.warning_threshold_percent > self.hard_stop_threshold_percent:
            raise ValueError("warning threshold cannot exceed hard-stop threshold")
        return self

class AiConfigurationUpdate(_Mutation):
    default_mode: Literal["single", "batch"] | None = None
    default_metadata_profile: str | None = Field(default=None, min_length=1, max_length=255)
    auto_analyze_new_assets: bool | None = None
    daily_item_limit: int | None = Field(default=None, ge=1, le=10_000)
    retry_count: int | None = Field(default=None, ge=0, le=20)
    timeout_seconds: int | None = Field(default=None, ge=1, le=3_600)

    @model_validator(mode="after")
    def require_change(self) -> "AiConfigurationUpdate":
        if all(getattr(self, name) is None for name in (
            "default_mode", "default_metadata_profile", "auto_analyze_new_assets",
            "daily_item_limit", "retry_count", "timeout_seconds",
        )):
            raise ValueError("At least one configuration field is required")
        return self

class AiJobMutation(_Mutation):
    force: bool = False


class AiBulkJobRetry(_Mutation):
    error_code: str = Field(min_length=1, max_length=100)
    limit: int = Field(default=100, ge=1, le=1000)


class CreativeGeminiCredentialRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=512)
    label: str | None = Field(default=None, max_length=255)
