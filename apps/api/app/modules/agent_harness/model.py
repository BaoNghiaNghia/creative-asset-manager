"""Tenant-authoritative Agent Harness policy catalog and evidence ledger.

Definitions stay disabled by default. This table does not start agents, confer
permissions, change worker routing, or promote memory.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKeyConstraint, Index, Integer, JSON, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentDefinitionModel(Base):
    __tablename__ = "cam_agent_definitions"
    __table_args__ = (
        CheckConstraint("max_concurrent >= 1 AND max_concurrent <= 32", name="ck_cam_agent_max_concurrent"),
        CheckConstraint("max_retries >= 0 AND max_retries <= 10", name="ck_cam_agent_max_retries"),
        Index("ix_cam_agent_definitions_tenant_created", "tenant_id", "created_at"),
    )

    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    version: Mapped[str] = mapped_column(String(20), primary_key=True)
    capabilities_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    allowed_tools_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    preferred_executor: Mapped[str] = mapped_column(String(96), nullable=False)
    fallback_executors_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    max_concurrent: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class AgentExperienceModel(Base):
    __tablename__ = "cam_agent_experiences"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_cam_experience_confidence"),
        CheckConstraint("sample_count >= 0", name="ck_cam_experience_sample_count"),
        CheckConstraint("status IN ('candidate', 'promoted', 'rejected')", name="ck_cam_experience_status"),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id", "agent_version"],
            ["cam_agent_definitions.tenant_id", "cam_agent_definitions.agent_id", "cam_agent_definitions.version"],
            name="fk_cam_experience_definition",
        ),
        Index(
            "ix_cam_experiences_eligible",
            "tenant_id", "agent_id", "agent_version", "scope_key", "status", "created_at",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(96), nullable=False)
    agent_version: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(160), nullable=False)
    rule_key: Mapped[str] = mapped_column(String(160), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_event_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    evidence_refs_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    verification_refs_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="candidate", server_default="candidate")
    evaluation_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
