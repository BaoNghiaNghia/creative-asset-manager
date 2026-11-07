"""Immutable, explicit-version Agent/Executor Registry for CAM.

This is a pure foundation module. No processing jobs, credentials, providers,
network calls, or production execution are performed here.

Agent permission policy and job version pinning must be enforced by the
authenticated CAM service when this registry is wired into the queue.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]{0,95}$")
_VERSION = re.compile(r"^v[1-9][0-9]*(?:\.[0-9]+){0,2}$")


class AgentRegistryError(ValueError):
    """Invalid, conflicting, or unrouteable agent definition."""


def _valid_identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise AgentRegistryError(f"invalid {label}")
    return value


def _immutable_ids(values: object, label: str) -> frozenset[str]:
    if isinstance(values, (str, bytes)) or values is None:
        raise AgentRegistryError(f"invalid {label}")
    try:
        items = frozenset(values)
    except (TypeError, ValueError) as exc:
        raise AgentRegistryError(f"invalid {label}") from exc
    for item in items:
        _valid_identifier(item, label)
    return items


@dataclass(frozen=True)
class AgentDefinition:
    agent_id: str
    version: str
    capabilities: frozenset[str]
    allowed_tools: frozenset[str]
    preferred_executor: str
    fallback_executors: tuple[str, ...] = ()
    max_concurrent: int = 1
    max_retries: int = 0

    def __post_init__(self) -> None:
        _valid_identifier(self.agent_id, "agent_id")
        if not isinstance(self.version, str) or not _VERSION.fullmatch(self.version):
            raise AgentRegistryError("invalid version")
        _valid_identifier(self.preferred_executor, "preferred_executor")
        object.__setattr__(self, "capabilities", _immutable_ids(self.capabilities, "capability"))
        object.__setattr__(self, "allowed_tools", _immutable_ids(self.allowed_tools, "allowed_tool"))
        if not self.capabilities:
            raise AgentRegistryError("agent must have at least one capability")
        if not isinstance(self.fallback_executors, (tuple, list)):
            raise AgentRegistryError("invalid fallbacks")
        fallback = tuple(self.fallback_executors)
        for item in fallback:
            _valid_identifier(item, "fallback_executor")
        if len(set(fallback)) != len(fallback) or self.preferred_executor in fallback:
            raise AgentRegistryError("duplicate or conflicting fallback executor")
        object.__setattr__(self, "fallback_executors", fallback)
        if type(self.max_concurrent) is not int or not 1 <= self.max_concurrent <= 32:
            raise AgentRegistryError("invalid max_concurrent")
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 10:
            raise AgentRegistryError("invalid max_retries")


@dataclass(frozen=True)
class ExecutorDefinition:
    executor_id: str
    capabilities: frozenset[str]
    healthy: bool = True
    available: bool = True

    def __post_init__(self) -> None:
        _valid_identifier(self.executor_id, "executor_id")
        object.__setattr__(self, "capabilities", _immutable_ids(self.capabilities, "capability"))
        if type(self.healthy) is not bool or type(self.available) is not bool:
            raise AgentRegistryError("executor health/availability must be bool")


class AgentRegistry:
    """Immutable version registrations; routing is a pre-dispatch decision only.

    A fallback is eligible only if a registered executor advertises all required
    capabilities and a fresh external health probe marked it healthy. This
    class intentionally does NOT retry or switch engines for a running job.
    """

    def __init__(self) -> None:
        self._agents: dict[tuple[str, str], AgentDefinition] = {}
        self._executors: dict[str, ExecutorDefinition] = {}

    def register_agent(self, agent: AgentDefinition) -> None:
        if not isinstance(agent, AgentDefinition):
            raise AgentRegistryError("invalid agent")
        key = (agent.agent_id, agent.version)
        if key in self._agents:
            raise AgentRegistryError("agent version already registered")
        self._agents[key] = agent

    def register_executor(self, executor: ExecutorDefinition) -> None:
        if not isinstance(executor, ExecutorDefinition):
            raise AgentRegistryError("invalid executor")
        if executor.executor_id in self._executors:
            raise AgentRegistryError("executor already registered")
        self._executors[executor.executor_id] = executor

    def agent(self, agent_id: str, version: str) -> AgentDefinition:
        """Require explicit version: never silently upgrade in-flight jobs."""
        try:
            return self._agents[(agent_id, version)]
        except (KeyError, TypeError) as exc:
            raise AgentRegistryError("unknown agent/version") from exc

    def route(self, agent_id: str, version: str, required: Iterable[str]) -> str:
        agent = self.agent(agent_id, version)
        requirements = _immutable_ids(required, "required_capability")
        if not requirements or not requirements.issubset(agent.capabilities):
            raise AgentRegistryError("required capabilities not permitted by agent")
        for executor_id in (agent.preferred_executor, *agent.fallback_executors):
            executor = self._executors.get(executor_id)
            if (executor is not None and executor.available and executor.healthy
                    and requirements.issubset(executor.capabilities)):
                return executor.executor_id
        raise AgentRegistryError("no healthy capable executor")

    def permitted_tool(self, agent_id: str, version: str, tool: str) -> bool:
        return isinstance(tool, str) and tool in self.agent(agent_id, version).allowed_tools
