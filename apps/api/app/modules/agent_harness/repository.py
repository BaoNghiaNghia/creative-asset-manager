"""Tenant-scoped Agent Harness persistence; does not commit or execute agents."""
from __future__ import annotations

import hashlib
import json
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .experience import ExperienceCandidate
from .model import AgentDefinitionModel, AgentExperienceModel
from .registry import AgentDefinition


def _tenant(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 255:
        raise ValueError("tenant_id is required")
    return value


def _definition_same(row: AgentDefinitionModel, agent: AgentDefinition) -> bool:
    return (
        set(row.capabilities_json) == set(agent.capabilities)
        and set(row.allowed_tools_json) == set(agent.allowed_tools)
        and row.preferred_executor == agent.preferred_executor
        and tuple(row.fallback_executors_json) == agent.fallback_executors
        and row.max_concurrent == agent.max_concurrent
        and row.max_retries == agent.max_retries
    )


def _event_key(candidate: ExperienceCandidate) -> str:
    # Immutable candidate identity includes tenant, agent version and source events;
    # retries do not create duplicate rows. This is NOT a secret/token fingerprint.
    payload = [
        candidate.tenant_id, candidate.agent_id, candidate.agent_version,
        candidate.scope_key, candidate.rule_key, sorted(candidate.event_ids),
    ]
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


class AgentHarnessRepository:
    """The API/service must authenticate the caller and enforce tenant RBAC.

    All reads and writes are explicitly scoped; DB primary keys also include
    tenant_id. Caller owns commit/rollback. No permission is inferred here.
    """

    def __init__(self, session: Session):
        self.session = session

    def get_definition(self, tenant_id: str, agent_id: str, version: str) -> AgentDefinitionModel | None:
        return self.session.get(AgentDefinitionModel, (_tenant(tenant_id), agent_id, version))

    def register_definition(
        self, tenant_id: str, agent: AgentDefinition, *, actor_id: str,
    ) -> AgentDefinitionModel:
        tenant_id = _tenant(tenant_id)
        if not isinstance(agent, AgentDefinition):
            raise ValueError("invalid agent")
        if not actor_id or not isinstance(actor_id, str) or len(actor_id) > 255:
            raise ValueError("actor_id is required")
        existing = self.get_definition(tenant_id, agent.agent_id, agent.version)
        if existing is not None:
            if not _definition_same(existing, agent):
                raise ValueError("immutable agent version conflict")
            return existing
        row = AgentDefinitionModel(
            tenant_id=tenant_id, agent_id=agent.agent_id, version=agent.version,
            capabilities_json=sorted(agent.capabilities),
            allowed_tools_json=sorted(agent.allowed_tools),
            preferred_executor=agent.preferred_executor,
            fallback_executors_json=list(agent.fallback_executors),
            max_concurrent=agent.max_concurrent, max_retries=agent.max_retries,
            enabled=False, created_by=actor_id,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_definitions(self, tenant_id: str, *, limit: int = 100) -> list[AgentDefinitionModel]:
        return list(self.session.scalars(
            select(AgentDefinitionModel)
            .where(AgentDefinitionModel.tenant_id == _tenant(tenant_id))
            .order_by(AgentDefinitionModel.agent_id, AgentDefinitionModel.version)
            .limit(max(1, min(limit, 100)))
        ).all())

    def record_candidate(self, candidate: ExperienceCandidate) -> AgentExperienceModel:
        if not isinstance(candidate, ExperienceCandidate):
            raise ValueError("invalid candidate")
        tenant_id = _tenant(candidate.tenant_id)
        if (not candidate.event_ids or len(candidate.event_ids) > 500
                or len(candidate.evidence_refs) > 100
                or len(candidate.verification_refs) > 100):
            raise ValueError("invalid evidence bounds")
        for item in (candidate.agent_id, candidate.agent_version,
                     candidate.scope_key, candidate.rule_key,
                     *candidate.event_ids, *candidate.evidence_refs, *candidate.verification_refs):
            if not isinstance(item, str) or not item.strip() or len(item) > 256:
                raise ValueError("invalid evidence reference")
        if self.get_definition(tenant_id, candidate.agent_id, candidate.agent_version) is None:
            raise ValueError("agent version is not registered for tenant")
        key = _event_key(candidate)
        existing = self.session.get(AgentExperienceModel, (tenant_id, key))
        if existing is not None:
            if (
                existing.confidence != candidate.confidence
                or existing.sample_count != candidate.sample_count
                or tuple(existing.evidence_refs_json) != candidate.evidence_refs
                or tuple(existing.verification_refs_json) != candidate.verification_refs
            ):
                raise ValueError("immutable evidence idempotency conflict")
            return existing
        row = AgentExperienceModel(
            tenant_id=tenant_id, event_key=key, agent_id=candidate.agent_id,
            agent_version=candidate.agent_version, scope_key=candidate.scope_key,
            rule_key=candidate.rule_key, confidence=candidate.confidence,
            sample_count=candidate.sample_count,
            source_event_ids_json=list(candidate.event_ids),
            evidence_refs_json=list(candidate.evidence_refs),
            verification_refs_json=list(candidate.verification_refs),
            status="candidate", evaluation_json={},
        )
        self.session.add(row)
        self.session.flush()
        return row

    def experience_counts(self, tenant_id: str) -> dict[str, int]:
        values = self.session.execute(
            select(AgentExperienceModel.status, func.count())
            .where(AgentExperienceModel.tenant_id == _tenant(tenant_id))
            .group_by(AgentExperienceModel.status)
        ).all()
        result = {"candidate": 0, "promoted": 0, "rejected": 0}
        for status, count in values:
            if status in result:
                result[status] = int(count)
        return result

    def eligible_experiences(
        self, tenant_id: str, agent_id: str, agent_version: str,
        scope_key: str, *, limit: int = 8,
    ) -> list[AgentExperienceModel]:
        return list(self.session.scalars(
            select(AgentExperienceModel).where(
                AgentExperienceModel.tenant_id == _tenant(tenant_id),
                AgentExperienceModel.agent_id == agent_id,
                AgentExperienceModel.agent_version == agent_version,
                AgentExperienceModel.scope_key == scope_key,
                AgentExperienceModel.status == "promoted",
            ).order_by(AgentExperienceModel.confidence.desc(), AgentExperienceModel.event_key)
            .limit(max(1, min(limit, 32)))
        ).all())
