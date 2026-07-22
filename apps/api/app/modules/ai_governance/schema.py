from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class BudgetPolicyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    daily_limit_micros: int | None = Field(default=None, ge=0)
    monthly_limit_micros: int | None = Field(default=None, ge=0)
    per_run_limit_micros: int | None = Field(default=None, ge=0)
    warning_threshold_percent: int | None = Field(default=None, ge=0, le=100)
    hard_stop_threshold_percent: int | None = Field(default=None, gt=0, le=100)
    timezone: Literal["UTC"] | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    action_on_limit: Literal["defer", "reject"] | None = None
    reason: str | None = Field(default=None, max_length=2000)

class CostRateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=255)
    processing_mode: Literal["any", "single", "batch"] = "any"
    effective_at: str
    input_unit_cost: float = Field(ge=0)
    output_unit_cost: float = Field(ge=0)
    media_unit_cost: float = Field(ge=0)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")

class RuntimeStopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stopped: bool
    reason: str = Field(min_length=1, max_length=2000)

class BudgetOverrideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analysis_id: str = Field(min_length=1, max_length=36)
    reason: str = Field(min_length=1, max_length=2000)
