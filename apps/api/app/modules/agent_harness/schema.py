"""Strict, metadata-only Agent Harness administration payloads.

This contract does not accept command lines, URLs, credentials, arbitrary
tool grants, enablement, prompts, or instructions for executing an agent.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .registry import AgentDefinition, AgentRegistryError

# Only explicitly reviewed metadata is storable; runtime adapters are not active.
KNOWN_CAPABILITIES = frozenset({
    "generate_text", "image_edit", "scout_discover", "feedback_analyze",
    "code_review",
})
KNOWN_TOOLS = frozenset({
    "brief.read", "design.read", "design.write", "feedback.read",
    "scout.read", "stage2.reference.read", "job.read", "code.read",
})
KNOWN_EXECUTORS = frozenset({"codex", "browser"})


class AgentCatalogRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    agent_id: str = Field(min_length=1, max_length=96, pattern=r"^[a-z][a-z0-9_.-]{0,95}$")
    version: str = Field(min_length=2, max_length=20, pattern=r"^v[1-9][0-9]*(?:\.[0-9]+){0,2}$")
    capabilities: list[str] = Field(min_length=1, max_length=6)
    allowed_tools: list[str] = Field(default_factory=list, max_length=8)
    preferred_executor: Literal["codex", "browser"]
    fallback_executors: list[Literal["codex", "browser"]] = Field(default_factory=list, max_length=1)
    max_concurrent: int = Field(default=1, ge=1, le=4, strict=True)
    max_retries: int = Field(default=0, ge=0, le=3, strict=True)
    reason: str = Field(min_length=6, max_length=200)

    @model_validator(mode="after")
    def enforce_catalog_boundaries(self):
        if not set(self.capabilities).issubset(KNOWN_CAPABILITIES):
            raise ValueError("unsupported agent capability")
        if not set(self.allowed_tools).issubset(KNOWN_TOOLS):
            raise ValueError("unsupported tool grant")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("duplicate capability")
        if len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError("duplicate tool")
        if any(ord(char) < 32 or ord(char) == 127 for char in self.reason):
            raise ValueError("invalid reason")
        if self.reason != self.reason.strip():
            raise ValueError("reason must be trimmed")
        return self

    def definition(self) -> AgentDefinition:
        return AgentDefinition(
            agent_id=self.agent_id,
            version=self.version,
            capabilities=frozenset(self.capabilities),
            allowed_tools=frozenset(self.allowed_tools),
            preferred_executor=self.preferred_executor,
            fallback_executors=tuple(self.fallback_executors),
            max_concurrent=self.max_concurrent,
            max_retries=self.max_retries,
        )


class Stage1FeedbackSyncRequest(BaseModel):
    """Explicit, bounded operator action; default preview never persists."""

    model_config = ConfigDict(extra="forbid")
    cohort: Literal["music", "western", "pets", "healthcare", "coffee", "family", "outdoors"]
    agent_version: str = Field(min_length=2, max_length=20, pattern=r"^v[1-9][0-9]*(?:[.][0-9]+){0,2}$")
    dry_run: bool = True


class AgentRoutePreviewRequest(BaseModel):
    """Read-only route simulation for RRUGC stages, never a job payload."""

    model_config = ConfigDict(extra="forbid")
    stage: Literal["stage1", "stage2", "stage4"]
    required_capabilities: list[Literal["generate_text", "image_edit"]] = Field(
        min_length=1, max_length=2,
    )
    required_tools: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_preflight(self):
        if len(set(self.required_capabilities)) != len(self.required_capabilities):
            raise ValueError("duplicate required capability")
        if len(set(self.required_tools)) != len(self.required_tools):
            raise ValueError("duplicate required tool")
        if not set(self.required_tools).issubset(KNOWN_TOOLS):
            raise ValueError("unrecognized required tool")
        return self
