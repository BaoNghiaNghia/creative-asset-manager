from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.creative_pipeline.model import CreativeSkillExecutionModel
from app.modules.creative_pipeline.skill_registry import ResolvedCreativeSkill


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GPTSkillExecutor:
    """Durable execution/audit boundary for GPT-backed Creative Pipeline nodes."""

    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def render_prompt(
        skill: ResolvedCreativeSkill,
        base_prompt: str,
        *,
        variables: Mapping[str, object] | None = None,
    ) -> str:
        instructions = skill.instructions
        for key, value in (variables or {}).items():
            instructions = instructions.replace("{{" + str(key) + "}}", str(value))
        return (
            f"--- GPT SKILL: {skill.name} v{skill.version} ---\n"
            f"{instructions.strip()}\n"
            "--- END GPT SKILL INSTRUCTIONS ---\n\n"
            f"{base_prompt}"
        )

    @staticmethod
    def snapshot_metadata(
        skill: ResolvedCreativeSkill,
        *,
        execution_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "executor_type": "gpt_skill",
            "skill_id": skill.skill_id,
            "skill_version_id": skill.skill_version_id,
            "skill_key": skill.skill_key,
            "skill_version": skill.version,
            "skill_binding_scope": skill.binding_scope,
            "skill_instructions_sha256": hashlib.sha256(
                skill.instructions.encode("utf-8")
            ).hexdigest(),
        }
        if execution_id:
            payload["skill_execution_id"] = execution_id
        return payload

    def get(
        self,
        *,
        tenant_id: str,
        execution_id: str | None,
        node_run_id: str | None = None,
    ) -> CreativeSkillExecutionModel | None:
        if not execution_id:
            return None
        statement = select(CreativeSkillExecutionModel).where(
            CreativeSkillExecutionModel.tenant_id == tenant_id,
            CreativeSkillExecutionModel.id == execution_id,
        )
        if node_run_id is not None:
            statement = statement.where(
                CreativeSkillExecutionModel.node_run_id == node_run_id
            )
        return self.session.scalar(statement)

    @staticmethod
    def _validate_existing(
        existing: CreativeSkillExecutionModel,
        *,
        skill: ResolvedCreativeSkill,
        idempotency_key: str,
        prompt_sha256: str,
        input_artifact_ids: list[str],
    ) -> CreativeSkillExecutionModel:
        if existing.skill_version_id != skill.skill_version_id:
            raise ValueError("skill_execution_version_conflict")
        if (
            existing.idempotency_key != idempotency_key[:255]
            or existing.prompt_sha256 != prompt_sha256
            or list(existing.input_artifact_ids_json or [])
            != list(input_artifact_ids)
        ):
            raise ValueError("skill_execution_snapshot_conflict")
        if existing.status in {"failed", "cancelled"}:
            raise ValueError("skill_execution_attempt_terminal")
        if existing.status == "completed":
            raise ValueError("skill_execution_output_inconsistent")
        return existing

    def start(
        self,
        *,
        tenant_id: str,
        pipeline_run_id: str,
        node_run_id: str,
        listing_task_id: str,
        attempt_number: int,
        skill: ResolvedCreativeSkill,
        variant_key: str,
        idempotency_key: str,
        input_artifact_ids: list[str],
        knowledge_snapshot_id: str | None,
        prompt_sha256: str,
        details: dict[str, Any] | None = None,
    ) -> CreativeSkillExecutionModel:
        attempt = max(1, int(attempt_number or 1))
        lookup = select(CreativeSkillExecutionModel).where(
            CreativeSkillExecutionModel.tenant_id == tenant_id,
            CreativeSkillExecutionModel.node_run_id == node_run_id,
            CreativeSkillExecutionModel.attempt_number == attempt,
            CreativeSkillExecutionModel.variant_key == variant_key,
        )
        existing = self.session.scalar(lookup)
        if existing is not None:
            return self._validate_existing(
                existing,
                skill=skill,
                idempotency_key=idempotency_key,
                prompt_sha256=prompt_sha256,
                input_artifact_ids=input_artifact_ids,
            )

        row = CreativeSkillExecutionModel(
            tenant_id=tenant_id,
            pipeline_run_id=pipeline_run_id,
            node_run_id=node_run_id,
            listing_task_id=listing_task_id,
            skill_id=skill.skill_id,
            skill_version_id=skill.skill_version_id,
            skill_key=skill.skill_key,
            skill_version=skill.version,
            binding_scope=skill.binding_scope,
            variant_key=variant_key,
            attempt_number=attempt,
            status="running",
            provider=skill.provider,
            model=skill.preferred_model,
            idempotency_key=idempotency_key[:255],
            input_artifact_ids_json=list(input_artifact_ids),
            output_artifact_ids_json=[],
            knowledge_snapshot_id=knowledge_snapshot_id,
            prompt_sha256=prompt_sha256,
            usage_json={},
            details_json={
                "skill_instructions_sha256": hashlib.sha256(
                    skill.instructions.encode("utf-8")
                ).hexdigest(),
                "knowledge_refs": list(skill.knowledge_refs),
                **dict(details or {}),
            },
            started_at=utcnow(),
        )
        try:
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
            return row
        except IntegrityError:
            # Lease races or duplicate delivery can attempt to create the same
            # immutable audit slot. Reuse it only when every execution snapshot
            # field still matches; otherwise surface a deterministic conflict.
            existing = self.session.scalar(lookup)
            if existing is None:
                raise ValueError("skill_execution_conflict")
            return self._validate_existing(
                existing,
                skill=skill,
                idempotency_key=idempotency_key,
                prompt_sha256=prompt_sha256,
                input_artifact_ids=input_artifact_ids,
            )

    def complete(
        self,
        row: CreativeSkillExecutionModel,
        *,
        provider: str,
        model: str | None,
        provider_request_id: str | None,
        usage: Mapping[str, Any] | None,
        output_artifact_ids: list[str],
        details: dict[str, Any] | None = None,
    ) -> CreativeSkillExecutionModel:
        row.status = "completed"
        row.provider = str(provider or row.provider)[:64]
        row.model = str(model)[:128] if model else None
        row.provider_request_id = (
            str(provider_request_id)[:512] if provider_request_id else None
        )
        row.usage_json = dict(usage or {})
        row.output_artifact_ids_json = list(output_artifact_ids)
        if details:
            row.details_json = {**(row.details_json or {}), **details}
        row.error_code = None
        row.error_message = None
        row.completed_at = row.completed_at or utcnow()
        self.session.flush()
        return row

    def fail_running_for_node(
        self,
        *,
        tenant_id: str,
        node_run_id: str,
        error_code: str,
        error_message: str,
    ) -> int:
        rows = list(
            self.session.scalars(
                select(CreativeSkillExecutionModel).where(
                    CreativeSkillExecutionModel.tenant_id == tenant_id,
                    CreativeSkillExecutionModel.node_run_id == node_run_id,
                    CreativeSkillExecutionModel.status == "running",
                )
            )
        )
        now = utcnow()
        for row in rows:
            row.status = "failed"
            row.error_code = str(error_code)[:100]
            row.error_message = str(error_message)[:1000]
            row.completed_at = row.completed_at or now
        self.session.flush()
        return len(rows)

    def cancel_running_for_node(
        self,
        *,
        tenant_id: str,
        node_run_id: str,
    ) -> int:
        rows = list(
            self.session.scalars(
                select(CreativeSkillExecutionModel).where(
                    CreativeSkillExecutionModel.tenant_id == tenant_id,
                    CreativeSkillExecutionModel.node_run_id == node_run_id,
                    CreativeSkillExecutionModel.status == "running",
                )
            )
        )
        now = utcnow()
        for row in rows:
            row.status = "cancelled"
            row.completed_at = row.completed_at or now
        self.session.flush()
        return len(rows)

    @staticmethod
    def summary(row: CreativeSkillExecutionModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "pipeline_run_id": row.pipeline_run_id,
            "node_run_id": row.node_run_id,
            "listing_task_id": row.listing_task_id,
            "skill_id": row.skill_id,
            "skill_version_id": row.skill_version_id,
            "skill_key": row.skill_key,
            "skill_version": row.skill_version,
            "binding_scope": row.binding_scope,
            "variant_key": row.variant_key,
            "attempt_number": row.attempt_number,
            "status": row.status,
            "provider": row.provider,
            "model": row.model,
            "input_artifact_ids": list(row.input_artifact_ids_json or []),
            "output_artifact_ids": list(row.output_artifact_ids_json or []),
            "knowledge_snapshot_id": row.knowledge_snapshot_id,
            "prompt_sha256": row.prompt_sha256,
            "provider_request_id": row.provider_request_id,
            "usage": dict(row.usage_json or {}),
            "details": dict(row.details_json or {}),
            "error_code": row.error_code,
            "error_message": row.error_message,
            "started_at": row.started_at,
            "completed_at": row.completed_at,
        }