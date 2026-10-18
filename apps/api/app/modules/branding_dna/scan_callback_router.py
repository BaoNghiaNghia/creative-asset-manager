"""Fenced callbacks for future authorized Branding DNA scan leases.

No job activation or dispatch is possible from these endpoints. They only
acknowledge progress/failure of an existing, server-issued, live lease.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.branding_dna.task_service import checkpoint_task, release_for_retry
from app.modules.branding_dna.worker_router import authenticate_agent

router = APIRouter(prefix="/api/v1/branding-dna", tags=["branding-dna-worker-scan-callbacks"])


class LeaseReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease_id: UUID
    lease_generation: int = Field(strict=True, ge=1, le=1_000_000)


class ScanCheckpoint(LeaseReceipt):
    checkpoint_seq: int = Field(strict=True, ge=0, le=1_000_000)


class ScanFailure(LeaseReceipt):
    error_code: Literal[
        "temporary_network_error", "rate_limited", "source_unavailable",
        "parser_changed", "manual_check_required",
    ]


@router.post("/workers/{agent_id}/scan-tasks/{task_id}/checkpoint")
def accept_checkpoint(
    agent_id: str, task_id: UUID, body: ScanCheckpoint, response: Response,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    """Report monotonic progress, never product content, URLs, or model output."""
    worker = authenticate_agent(session, agent_id, authorization)
    result = checkpoint_task(
        session, tenant_id=worker.tenant_id, task_id=str(task_id),
        agent_id=worker.id, lease_id=str(body.lease_id),
        lease_generation=body.lease_generation, checkpoint_seq=body.checkpoint_seq,
    )
    response.headers["Cache-Control"] = "no-store"
    return {"accepted": True, "scan_enabled": False, **result}


@router.post("/workers/{agent_id}/scan-tasks/{task_id}/failure")
def accept_failure(
    agent_id: str, task_id: UUID, body: ScanFailure, response: Response,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    """Classified failure only. Does not import evidence or mark a task complete."""
    worker = authenticate_agent(session, agent_id, authorization)
    result = release_for_retry(
        session, tenant_id=worker.tenant_id, task_id=str(task_id),
        agent_id=worker.id, lease_id=str(body.lease_id),
        lease_generation=body.lease_generation, error_code=body.error_code,
    )
    response.headers["Cache-Control"] = "no-store"
    return {"accepted": True, "scan_enabled": False, **result}
