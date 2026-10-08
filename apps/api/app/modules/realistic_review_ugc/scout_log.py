from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from threading import Lock
from time import monotonic

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.application_logs.model import LogApplicationModel
from app.modules.application_logs.repository import ApplicationLogRepository
from app.modules.realistic_review_ugc.scout_automation import RrugcAutoScoutService


SCOUT_LOG_APPLICATION_SLUG = "rrugc-scout"
SCOUT_LOG_RETENTION_DAYS = 5
SCOUT_LOG_PURGE_INTERVAL_SECONDS = 600


class RrugcScoutLogService:
    """Persist authenticated Scout diagnostics in the shared application log store."""

    _purge_guard = Lock()
    _last_purge: dict[str, float] = {}

    @classmethod
    def _purge_due(cls, tenant_id: str, *, now: float | None = None) -> bool:
        current = monotonic() if now is None else now
        with cls._purge_guard:
            last = cls._last_purge.get(tenant_id)
            if last is not None and 0 <= current - last < SCOUT_LOG_PURGE_INTERVAL_SECONDS:
                return False
            cls._last_purge[tenant_id] = current
            return True

    def __init__(self, session: Session):
        self.session = session
        self.repository = ApplicationLogRepository(session)

    def _application(self, tenant_id: str) -> LogApplicationModel:
        application = self.session.scalar(
            select(LogApplicationModel).where(
                LogApplicationModel.tenant_id == tenant_id,
                LogApplicationModel.slug == SCOUT_LOG_APPLICATION_SLUG,
            )
        )
        if application is not None:
            return application

        application, _discarded_api_key = self.repository.create_application(
            tenant_id=tenant_id,
            slug=SCOUT_LOG_APPLICATION_SLUG,
            display_name="RRUGC Scout Logs",
            payload_schema=None,
        )
        return application

    def ingest(
        self,
        *,
        agent_id: str,
        raw_token: str,
        events: list[dict],
    ) -> tuple[int, int]:
        agent = RrugcAutoScoutService(self.session).authenticate_agent(
            agent_id=agent_id,
            raw_token=raw_token,
        )
        application = self._application(agent.tenant_id)
        now = datetime.now(timezone.utc)
        # Expiry is also covered by the retention worker; do not run a
        # tenant-wide DELETE for every Scout event batch.
        if self._purge_due(agent.tenant_id):
            self.repository.purge_expired(now=now, tenant_id=agent.tenant_id)

        created = 0
        for event in events:
            event_id = str(event["event_id"])
            event_type = str(event["event_type"])
            occurred_at = event.get("occurred_at")
            level = str(event.get("level") or "info")
            payload = dict(event.get("payload") or {})
            payload.setdefault("agent_id", agent.id)
            payload.setdefault("agent_name", agent.name)
            payload.setdefault("machine_label", agent.machine_label)

            request_hash = hashlib.sha256(
                json.dumps(
                    {
                        "event_type": event_type,
                        "level": level,
                        "occurred_at": (
                            occurred_at.isoformat()
                            if hasattr(occurred_at, "isoformat")
                            else occurred_at
                        ),
                        "payload": payload,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
            _row, was_created = self.repository.create_log(
                application=application,
                idempotency_key=event_id,
                request_hash=request_hash,
                level=level,
                event_type=event_type,
                message=None,
                trace_id=agent.id,
                payload=payload,
                occurred_at=occurred_at,
                now=now,
                retention_days=SCOUT_LOG_RETENTION_DAYS,
            )
            created += int(was_created)

        self.session.commit()
        return len(events), created
