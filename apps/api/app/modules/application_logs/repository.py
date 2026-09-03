from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.modules.application_logs.model import ApplicationLogModel, LogApplicationModel
from app.modules.application_logs.schema import LOG_RETENTION_DAYS


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    return f"camlog_{secrets.token_urlsafe(36)}"


class IdempotencyConflictError(RuntimeError):
    pass


class ApplicationLogRepository:
    def __init__(self, session: Session): self.session = session

    def create_application(self, *, tenant_id: str, slug: str, display_name: str, payload_schema: dict | None) -> tuple[LogApplicationModel, str]:
        raw_key = generate_api_key()
        application = LogApplicationModel(tenant_id=tenant_id, slug=slug, display_name=display_name, payload_schema_json=payload_schema, key_prefix=raw_key[:14], secret_hash=hash_api_key(raw_key))
        self.session.add(application); self.session.flush()
        return application, raw_key

    def authenticate(self, raw_key: str) -> LogApplicationModel | None:
        if not raw_key.startswith("camlog_") or len(raw_key) < 32 or len(raw_key) > 512: return None
        return self.session.scalar(select(LogApplicationModel).where(LogApplicationModel.secret_hash == hash_api_key(raw_key), LogApplicationModel.active.is_(True), LogApplicationModel.revoked_at.is_(None)))

    def rotate_key(self, application: LogApplicationModel) -> str:
        raw_key = generate_api_key()
        application.key_prefix = raw_key[:14]; application.secret_hash = hash_api_key(raw_key); application.updated_at = datetime.now(timezone.utc)
        self.session.flush(); return raw_key

    def purge_expired(self, *, now: datetime | None = None, tenant_id: str | None = None) -> int:
        statement = delete(ApplicationLogModel).where(ApplicationLogModel.expires_at <= (now or datetime.now(timezone.utc)))
        if tenant_id is not None: statement = statement.where(ApplicationLogModel.tenant_id == tenant_id)
        result = self.session.execute(statement); return int(result.rowcount or 0)

    def create_log(self, *, application: LogApplicationModel, idempotency_key: str | None, request_hash: str, level: str, event_type: str, message: str | None, trace_id: str | None, payload: dict, occurred_at: datetime | None, now: datetime | None = None) -> tuple[ApplicationLogModel, bool]:
        if idempotency_key:
            existing = self.session.scalar(select(ApplicationLogModel).where(ApplicationLogModel.application_id == application.id, ApplicationLogModel.idempotency_key == idempotency_key))
            if existing is not None:
                if existing.request_hash != request_hash: raise IdempotencyConflictError(idempotency_key)
                return existing, False
        received_at = now or datetime.now(timezone.utc)
        log = ApplicationLogModel(tenant_id=application.tenant_id, application_id=application.id, idempotency_key=idempotency_key, request_hash=request_hash, level=level, event_type=event_type, message=message, trace_id=trace_id, payload_json=payload, occurred_at=occurred_at or received_at, received_at=received_at, expires_at=received_at + timedelta(days=LOG_RETENTION_DAYS))
        self.session.add(log); self.session.flush(); return log, True

    def list_logs(self, *, application_id: str, now: datetime, start: datetime | None, end: datetime | None, level: str | None, event_type: str | None, trace_id: str | None, limit: int, offset: int) -> tuple[list[ApplicationLogModel], int]:
        filters = [ApplicationLogModel.application_id == application_id, ApplicationLogModel.expires_at > now]
        if start is not None: filters.append(ApplicationLogModel.occurred_at >= start)
        if end is not None: filters.append(ApplicationLogModel.occurred_at <= end)
        if level is not None: filters.append(ApplicationLogModel.level == level)
        if event_type is not None: filters.append(ApplicationLogModel.event_type == event_type)
        if trace_id is not None: filters.append(ApplicationLogModel.trace_id == trace_id)
        total = int(self.session.scalar(select(func.count()).select_from(ApplicationLogModel).where(*filters)) or 0)
        rows = list(self.session.scalars(select(ApplicationLogModel).where(*filters).order_by(ApplicationLogModel.received_at.desc(), ApplicationLogModel.id.desc()).offset(offset).limit(limit)))
        return rows, total
