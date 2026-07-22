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
                "single_enabled", "batch_enabled", "active_jobs_limit",
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

    @model_validator(mode="after")
    def validate_limits(self) -> "AiBudgetUpdate":
        if self.enabled and self.daily_limit_micros is None and self.monthly_limit_micros is None:
            raise ValueError("An enabled budget requires a daily or monthly limit")
        return self


class AiJobMutation(_Mutation):
    pass
