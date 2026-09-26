from __future__ import annotations

import re
from datetime import date
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.persistence_model import (
    InventoryDailyCarryForwardModel,
    InventoryOperationAuditModel,
    InventoryOperationChangeModel,
    inventory_utcnow,
)

_STAGE_NAMES = {
    "morning_reset": "Reset đầu ngày",
    "afternoon_snapshot": "Snapshot",
    "evening_reconcile": "Đối soát Gemini",
    "manual_prompt_test": "Gemini manual test",
}
_STAGE_JOB_TYPES = {
    "morning_reset": "inventory_v5_morning_reset_slot",
    "afternoon_snapshot": "inventory_v5_afternoon_snapshot_slot",
    "evening_reconcile": "inventory_v5_evening_reconcile_slot",
}


def _row_number(cell: str | None) -> int | None:
    match = re.fullmatch(r"[A-Za-z]+([1-9][0-9]*)", str(cell or ""))
    return int(match.group(1)) if match else None


def _audit_view(row: InventoryOperationAuditModel, changes: list[InventoryOperationChangeModel]) -> dict[str, Any]:
    return {
        "id": row.id,
        "business_date": row.business_date.isoformat(),
        "stage": row.stage,
        "stage_label": _STAGE_NAMES.get(row.stage, row.stage),
        "run_id": row.run_id,
        "status": row.status,
        "summary": dict(row.summary_json or {}),
        "assessment": dict(row.assessment_json or {}),
        "tool_trace": list(row.tool_trace_json or []),
        "read_ranges": list(row.read_ranges_json or []),
        "prompt": {
            "source": row.prompt_source,
            "version": row.prompt_version,
            "hash": row.prompt_hash,
        },
        "knowledge": {
            "hash": row.knowledge_hash,
            "version": row.knowledge_version,
        },
        "model": row.model,
        "writes": row.writes,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "changes": [
            {
                "sequence": change.sequence,
                "sheet": change.sheet,
                "row_number": change.row_number,
                "cell": change.cell,
                "before": (change.before_json or {}).get("value"),
                "after": (change.after_json or {}).get("value"),
                "source_sheet": change.source_sheet,
                "source_cell": change.source_cell,
                "material_id": change.material_id,
                "warehouse_id": change.warehouse_id,
                "operation_type": change.operation_type,
                "reason": change.reason,
                "provenance": change.provenance,
                "evidence": list(change.evidence_json or []),
                "verification_status": change.verification_status,
            }
            for change in changes
        ],
        "source": "operation_audit",
    }


class InventoryOperationAuditService:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def persist_v4_result(
        self,
        tenant_id: str,
        business_date: date,
        *,
        stage: str,
        result: Any,
        started_at=None,
    ) -> dict[str, Any]:
        run_id = str(getattr(result, "run_id", "") or "") or None
        status = str(getattr(result, "status", "failed") or "failed")
        if status not in {"shadow", "completed", "review_required", "blocked", "failed"}:
            status = "failed"
        change_audit = list(getattr(result, "change_audit", None) or [])
        staged = getattr(result, "staged", None)
        execution = dict(getattr(result, "execution", None) or {})
        summary = {
            "tool_rounds": int(getattr(result, "tool_rounds", 0) or 0),
            "read_calls": int(getattr(result, "read_calls", 0) or 0),
            "read_cells": int(getattr(result, "read_cells", 0) or 0),
            "operation_count": len(getattr(staged, "operations", []) or []),
            "issue_count": len(getattr(staged, "issues", []) or []),
            "material_action_count": len(getattr(staged, "material_actions", []) or []),
            "knowledge_proposal_count": len(getattr(result, "knowledge_proposals", []) or []),
            "writes": int(getattr(result, "writes", 0) or 0),
            "verification_status": execution.get("verification_status"),
            "plan_hash": getattr(result, "plan_hash", None),
            "staged_summary": getattr(staged, "summary", "") if staged is not None else "",
        }
        assessment = getattr(result, "assessment", None)
        if hasattr(assessment, "model_dump"):
            assessment_json = assessment.model_dump(mode="json")
        elif isinstance(assessment, dict):
            assessment_json = assessment
        else:
            assessment_json = {}
        completed_at = inventory_utcnow()

        with self.session_factory() as session:
            row = None
            if run_id:
                row = session.scalar(
                    select(InventoryOperationAuditModel).where(
                        InventoryOperationAuditModel.tenant_id == tenant_id,
                        InventoryOperationAuditModel.business_date == business_date,
                        InventoryOperationAuditModel.stage == stage,
                        InventoryOperationAuditModel.run_id == run_id,
                    )
                )
            if row is None:
                row = InventoryOperationAuditModel(
                    tenant_id=tenant_id,
                    business_date=business_date,
                    stage=stage,
                    run_id=run_id,
                    status=status,
                    summary_json=summary,
                    assessment_json=assessment_json,
                    tool_trace_json=list(getattr(result, "tool_trace", []) or []),
                    read_ranges_json=list(getattr(result, "ranges_read", []) or []),
                    prompt_source=getattr(result, "business_prompt_source", None),
                    prompt_version=getattr(result, "business_prompt_version", None),
                    prompt_hash=getattr(result, "business_prompt_hash", None),
                    knowledge_hash=getattr(result, "knowledge_hash", None),
                    knowledge_version=getattr(result, "knowledge_version", None),
                    model=getattr(result, "model", None),
                    writes=int(getattr(result, "writes", 0) or 0),
                    started_at=started_at,
                    completed_at=completed_at,
                )
                session.add(row)
                session.flush()
            else:
                row.status = status
                row.summary_json = summary
                row.assessment_json = assessment_json
                row.tool_trace_json = list(getattr(result, "tool_trace", []) or [])
                row.read_ranges_json = list(getattr(result, "ranges_read", []) or [])
                row.prompt_source = getattr(result, "business_prompt_source", None)
                row.prompt_version = getattr(result, "business_prompt_version", None)
                row.prompt_hash = getattr(result, "business_prompt_hash", None)
                row.knowledge_hash = getattr(result, "knowledge_hash", None)
                row.knowledge_version = getattr(result, "knowledge_version", None)
                row.model = getattr(result, "model", None)
                row.writes = int(getattr(result, "writes", 0) or 0)
                row.completed_at = completed_at
                row.updated_at = completed_at
                session.execute(
                    delete(InventoryOperationChangeModel).where(
                        InventoryOperationChangeModel.audit_id == row.id
                    )
                )

            verification_default = (
                "verified"
                if execution.get("verification_status") == "verified"
                else "not_executed"
                if execution.get("verification_status") == "not_executed"
                else "unknown"
            )
            for index, item in enumerate(change_audit, start=1):
                cell = str(item.get("cell") or "")
                session.add(
                    InventoryOperationChangeModel(
                        tenant_id=tenant_id,
                        audit_id=row.id,
                        sequence=index,
                        sheet=str(item.get("sheet") or ""),
                        row_number=item.get("row_number") or _row_number(cell),
                        cell=cell,
                        before_json={"value": item.get("before")},
                        after_json={"value": item.get("after")},
                        source_sheet=item.get("source_sheet"),
                        source_cell=item.get("source_cell"),
                        material_id=item.get("material_id"),
                        warehouse_id=item.get("warehouse_id"),
                        operation_type=str(item.get("operation_type") or "set_cell"),
                        reason=str(item.get("reason") or ""),
                        provenance=item.get("provenance"),
                        evidence_json=list(item.get("evidence") or []),
                        verification_status=str(item.get("verification_status") or verification_default),
                    )
                )
            session.commit()
            audit_id = row.id
        return self.get_by_id(tenant_id, audit_id)

    def persist_failure(
        self,
        tenant_id: str,
        business_date: date,
        *,
        stage: str,
        error: Exception,
        run_id: str | None = None,
        started_at=None,
    ) -> dict[str, Any]:
        context = getattr(error, "inventory_audit_context", None)
        context = context if isinstance(context, dict) else {}
        summary = {
            "tool_rounds": int(context.get("tool_rounds") or 0),
            "read_calls": int(context.get("read_calls") or 0),
            "read_cells": int(context.get("read_cells") or 0),
            "operation_count": int(context.get("operation_count") or 0),
            "issue_count": int(context.get("issue_count") or 0),
            "knowledge_proposal_count": int(context.get("knowledge_proposal_count") or 0),
            "writes": 0,
            "verification_status": "failed",
            "staged_summary": str(context.get("staged_summary") or ""),
        }
        row = InventoryOperationAuditModel(
            tenant_id=tenant_id,
            business_date=business_date,
            stage=stage,
            run_id=run_id,
            status="failed",
            summary_json=summary,
            assessment_json=dict(context.get("assessment") or {}),
            tool_trace_json=list(context.get("tool_trace") or []),
            read_ranges_json=list(context.get("ranges_read") or []),
            prompt_source=context.get("prompt_source"),
            prompt_version=context.get("prompt_version"),
            prompt_hash=context.get("prompt_hash"),
            knowledge_hash=context.get("knowledge_hash"),
            knowledge_version=context.get("knowledge_version"),
            model=context.get("model"),
            writes=0,
            error_code=str(getattr(error, "code", type(error).__name__))[:100],
            error_message=str(error)[:1000],
            started_at=started_at,
            completed_at=inventory_utcnow(),
        )
        with self.session_factory() as session:
            session.add(row)
            session.flush()
            for index, item in enumerate(list(context.get("change_audit") or []), start=1):
                cell = str(item.get("cell") or "")
                session.add(
                    InventoryOperationChangeModel(
                        tenant_id=tenant_id,
                        audit_id=row.id,
                        sequence=index,
                        sheet=str(item.get("sheet") or ""),
                        row_number=item.get("row_number") or _row_number(cell),
                        cell=cell,
                        before_json={"value": item.get("before")},
                        after_json={"value": item.get("after")},
                        source_sheet=item.get("source_sheet"),
                        source_cell=item.get("source_cell"),
                        material_id=item.get("material_id"),
                        warehouse_id=item.get("warehouse_id"),
                        operation_type=str(item.get("operation_type") or "set_cell"),
                        reason=str(item.get("reason") or ""),
                        provenance=item.get("provenance"),
                        evidence_json=list(item.get("evidence") or []),
                        verification_status="failed",
                    )
                )
            session.commit()
            session.refresh(row)
            audit_id = row.id
        return self.get_by_id(tenant_id, audit_id)

    def get_by_id(self, tenant_id: str, audit_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            row = session.scalar(
                select(InventoryOperationAuditModel).where(
                    InventoryOperationAuditModel.tenant_id == tenant_id,
                    InventoryOperationAuditModel.id == audit_id,
                )
            )
            if row is None:
                raise LookupError("inventory_operation_audit_not_found")
            changes = list(
                session.scalars(
                    select(InventoryOperationChangeModel)
                    .where(
                        InventoryOperationChangeModel.tenant_id == tenant_id,
                        InventoryOperationChangeModel.audit_id == row.id,
                    )
                    .order_by(InventoryOperationChangeModel.sequence)
                )
            )
            return _audit_view(row, changes)

    def stage_detail(self, tenant_id: str, business_date: date, stage: str) -> dict[str, Any]:
        if stage not in _STAGE_NAMES:
            raise ValueError("invalid_inventory_lifecycle_stage")
        with self.session_factory() as session:
            # Morning Reset has a separate trusted writer. Its persisted
            # carry-forward plan is the authoritative source of what actually
            # targeted the shared workbook, so prefer it over a generic V4 audit.
            row = None if stage == "morning_reset" else session.scalar(
                select(InventoryOperationAuditModel)
                .where(
                    InventoryOperationAuditModel.tenant_id == tenant_id,
                    InventoryOperationAuditModel.business_date == business_date,
                    InventoryOperationAuditModel.stage == stage,
                )
                .order_by(InventoryOperationAuditModel.created_at.desc())
                .limit(1)
            )
            if row is not None:
                changes = list(
                    session.scalars(
                        select(InventoryOperationChangeModel)
                        .where(
                            InventoryOperationChangeModel.tenant_id == tenant_id,
                            InventoryOperationChangeModel.audit_id == row.id,
                        )
                        .order_by(InventoryOperationChangeModel.sequence)
                    )
                )
                return _audit_view(row, changes)

            if stage == "morning_reset":
                carry = session.scalar(
                    select(InventoryDailyCarryForwardModel).where(
                        InventoryDailyCarryForwardModel.tenant_id == tenant_id,
                        InventoryDailyCarryForwardModel.target_business_date == business_date,
                    )
                )
                if carry is not None:
                    plan = dict(carry.plan_json or {})
                    operations = list(plan.get("operations") or [])
                    changes = []
                    for index, item in enumerate(operations, start=1):
                        target = dict(item.get("target") or {})
                        source = dict(item.get("source") or {})
                        cell = str(target.get("cell") or "")
                        semantic = dict(item.get("semantic_context") or {})
                        changes.append(
                            {
                                "sequence": index,
                                "sheet": str(target.get("sheet") or ""),
                                "row_number": _row_number(cell),
                                "cell": cell,
                                "before": target.get("raw_value", target.get("current_value")),
                                "after": None if item.get("type") == "clear_cell" else item.get("value", target.get("opening_value")),
                                "source_sheet": source.get("sheet"),
                                "source_cell": source.get("cell"),
                                "material_id": item.get("material_id"),
                                "warehouse_id": item.get("warehouse_id"),
                                "operation_type": str(item.get("type") or "set_cell"),
                                "reason": str(semantic.get("reason") or semantic.get("rule") or "carry_forward"),
                                "provenance": "exact_copy" if item.get("type") != "clear_cell" else None,
                                "evidence": [
                                    value
                                    for value in (
                                        {"role": "source", **source} if source else None,
                                        {"role": "target", **target} if target else None,
                                    )
                                    if value
                                ],
                                "verification_status": "verified" if carry.verified_at else "unknown",
                            }
                        )
                    audit = dict(plan.get("audit") or {})
                    return {
                        "id": carry.id,
                        "business_date": business_date.isoformat(),
                        "stage": stage,
                        "stage_label": _STAGE_NAMES[stage],
                        "run_id": carry.id,
                        "status": carry.status,
                        "summary": {
                            "operation_count": len(operations),
                            "issue_count": len(plan.get("issues") or []),
                            "material_count": carry.material_count,
                            "warehouse_count": carry.warehouse_count,
                            "plan_hash": carry.plan_hash,
                            "verification_status": "verified" if carry.verified_at else "unknown",
                            "tool_rounds": int(audit.get("tool_rounds") or 0),
                            "evidence_cell_count": int(audit.get("evidence_cell_count") or 0),
                        },
                        "assessment": {},
                        "tool_trace": list(audit.get("tool_trace") or []),
                        "read_ranges": list(audit.get("read_ranges") or []),
                        "prompt": {
                            "source": carry.prompt_source,
                            "version": carry.prompt_version,
                            "hash": carry.prompt_hash,
                        },
                        "knowledge": {
                            "hash": getattr(carry, "knowledge_hash", None),
                            "version": getattr(carry, "knowledge_version", None),
                        },
                        "model": audit.get("model"),
                        "writes": len(operations) if carry.applied_at else 0,
                        "error_code": carry.error_code,
                        "error_message": carry.error_message,
                        "started_at": carry.started_at.isoformat() if carry.started_at else None,
                        "completed_at": carry.completed_at.isoformat() if carry.completed_at else None,
                        "changes": changes,
                        "issues": list(plan.get("issues") or []),
                        "source": "carry_forward_plan",
                    }
                job_type = _STAGE_JOB_TYPES.get(stage)
                if job_type:
                    job = session.scalar(
                        select(InventoryJobModel)
                        .where(
                            InventoryJobModel.tenant_id == tenant_id,
                            InventoryJobModel.job_type == job_type,
                            InventoryJobModel.entity_id == f"{business_date.isoformat()}:{stage}",
                        )
                        .order_by(InventoryJobModel.created_at.desc())
                        .limit(1)
                    )
                    if job is not None:
                        return {
                            "id": job.id,
                            "business_date": business_date.isoformat(),
                            "stage": stage,
                            "stage_label": _STAGE_NAMES[stage],
                            "run_id": job.id,
                            "status": job.status,
                            "summary": {
                                "attempt_count": job.attempt_count,
                                "max_attempts": job.max_attempts,
                                "scheduler_status": job.status,
                            },
                            "assessment": {},
                            "tool_trace": [],
                            "read_ranges": [],
                            "prompt": {"source": None, "version": None, "hash": None},
                            "knowledge": {"hash": None, "version": None},
                            "model": None,
                            "writes": 0,
                            "error_code": job.last_error_code,
                            "error_message": job.last_error_message,
                            "started_at": job.claimed_at.isoformat() if job.claimed_at else None,
                            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                            "changes": [],
                            "issues": [],
                            "source": "scheduler_job",
                        }
        raise LookupError("inventory_operation_audit_not_found")
