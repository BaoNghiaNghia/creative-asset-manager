"""Durable, idempotent lifecycle event acknowledgements for dedicated workers.

Bearer-scoped ingestion only; no API for product/gallery upload, job submission,
browser navigation, profile access, or worker command execution.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.branding_dna.model import now_utc
from app.modules.branding_dna.worker_event_model import BrandWorkerEventModel
from app.modules.branding_dna.worker_router import authenticate_agent

router = APIRouter(prefix="/api/v1/branding-dna", tags=["branding-dna-worker-events"])

EVENT_CODES = {
    "started": {"worker_start"},
    "stopped": {"graceful_shutdown"},
    "heartbeat_failure": {"network_error", "server_4xx", "server_5xx"},
    "heartbeat_recovered": {"recovered"},
}
MAX_EVENTS = 50
MAX_NEW_EVENTS_PER_DAY = 1000


class WorkerLifecycleEvent(BaseModel):
    model_config = {"extra": "forbid", "str_strip_whitespace": True}
    event_id: UUID
    kind: Literal["started", "stopped", "heartbeat_failure", "heartbeat_recovered"]
    detail_code: Literal[
        "worker_start", "graceful_shutdown", "network_error",
        "server_4xx", "server_5xx", "recovered",
    ]
    occurred_at: datetime

    @model_validator(mode="after")
    def restrict_event(self):
        if self.detail_code not in EVENT_CODES[self.kind]:
            raise ValueError("Invalid lifecycle event/detail combination")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("Event timestamp must have a timezone")
        return self


class WorkerEventBatch(BaseModel):
    model_config = {"extra": "forbid"}
    events: list[WorkerLifecycleEvent] = Field(min_length=1, max_length=MAX_EVENTS)

    @model_validator(mode="after")
    def reject_duplicate_ids(self):
        ids = [event.event_id for event in self.events]
        if len(ids) != len(set(ids)):
            raise ValueError("Repeated event_id inside one batch")
        return self


def _digest(event: WorkerLifecycleEvent) -> str:
    serialized = json.dumps(event.model_dump(mode="json"), sort_keys=True,
                            ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@router.post("/workers/{agent_id}/events")
def ingest_worker_events(
    agent_id: str, body: WorkerEventBatch, response: Response,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    worker = authenticate_agent(session, agent_id, authorization)
    tenant_id = worker.tenant_id
    now = now_utc()
    # Retention/replay boundary. No free text, URL, token or source information
    # is accepted, even when an authorized worker supplies it.
    oldest = now - timedelta(days=30)
    newest = now + timedelta(minutes=5)
    for event in body.events:
        at = event.occurred_at.astimezone(timezone.utc)
        if at < oldest or at > newest:
            raise HTTPException(422, detail="Lifecycle event outside acceptance window")

    ids = [str(event.event_id) for event in body.events]
    saved = session.scalars(select(BrandWorkerEventModel).where(
        BrandWorkerEventModel.tenant_id == tenant_id,
        BrandWorkerEventModel.agent_id == agent_id,
        BrandWorkerEventModel.event_id.in_(ids),
    )).all()
    existing = {row.event_id: row for row in saved}
    for event in body.events:
        row = existing.get(str(event.event_id))
        if row is not None and row.event_sha256 != _digest(event):
            raise HTTPException(409, detail="event_id replay conflicts with stored event")

    to_add = [event for event in body.events if str(event.event_id) not in existing]
    if to_add:
        last_day = session.scalar(select(func.count()).select_from(BrandWorkerEventModel).where(
            BrandWorkerEventModel.tenant_id == tenant_id,
            BrandWorkerEventModel.agent_id == agent_id,
            BrandWorkerEventModel.received_at >= now - timedelta(days=1),
        )) or 0
        if last_day + len(to_add) > MAX_NEW_EVENTS_PER_DAY:
            raise HTTPException(429, detail="Worker event daily quota exceeded")
    for event in to_add:
        session.add(BrandWorkerEventModel(
            tenant_id=tenant_id, agent_id=agent_id, event_id=str(event.event_id),
            kind=event.kind, detail_code=event.detail_code,
            event_sha256=_digest(event), occurred_at=event.occurred_at,
        ))
    if to_add:
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            # Do not ACK a batch that did not commit fully. A retry will
            # re-read the unique event IDs and ACK if all hashes match.
            raise HTTPException(409, detail="Concurrent event ingest: retry safely") from exc
    response.headers["Cache-Control"] = "no-store"
    return {"accepted_ids": ids, "accepted_count": len(ids),
            "scan_enabled": False, "replayed_count": len(existing)}
