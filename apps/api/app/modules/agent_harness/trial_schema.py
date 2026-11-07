"""Strict manual study registration / keyword reservation payload contracts.

No policy/agent execution, credentials, arbitrary prompts or user-supplied arm.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class HoldoutStudyRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    study_id: str = Field(min_length=4, max_length=64, pattern=r"^[a-z][a-z0-9_.-]{3,63}$")
    cohort: Literal["music", "western", "pets", "healthcare", "coffee", "family", "outdoors"]
    candidate_event_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    trial_version: str = Field(max_length=20, pattern=r"^v[1-9][0-9]*(?:[.][0-9]+){0,2}$")
    holdout_percent: int = Field(default=20, ge=10, le=40, strict=True)
    reason: str = Field(min_length=10, max_length=200)
    dry_run: bool = Field(default=True, strict=True)

    @field_validator("reason")
    @classmethod
    def reason_is_safe(cls, value: str) -> str:
        if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("invalid reason")
        return value


class HoldoutKeywordReservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    keyword_ids: list[str] = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=10, max_length=200)
    dry_run: bool = Field(default=True, strict=True)

    @field_validator("keyword_ids")
    @classmethod
    def distinct_keywords(cls, value: list[str]) -> list[str]:
        for item in value:
            try:
                if str(UUID(item)) != item:
                    raise ValueError("non-canonical keyword UUID")
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("invalid keyword UUID") from exc
        if len(set(value)) != len(value):
            raise ValueError("duplicate keyword IDs")
        return value

    @field_validator("reason")
    @classmethod
    def reason_is_safe(cls, value: str) -> str:
        if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("invalid reason")
        return value
