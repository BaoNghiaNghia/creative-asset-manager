from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.modules.ai_governance.model import GeminiProjectQuotaStateModel

_PACIFIC_TIME = ZoneInfo("America/Los_Angeles")
_PROJECT_TOTAL_MODEL = "__project_total__"

@dataclass(frozen=True, slots=True)
class GeminiQuotaDecision:
    allowed: bool
    reason: str | None = None
    available_at: datetime | None = None

class GeminiProjectQuotaRepository:
    """Atomically reserve Gemini RPD capacity for one Google-project scope."""

    def __init__(self, session: Session):
        self.session = session

    def reserve_request(
        self,
        *,
        quota_scope: str,
        model: str,
        rpd: int,
        project_rpd: int | None = None,
        now: datetime | None = None,
    ) -> GeminiQuotaDecision:
        if not quota_scope.strip() or not model.strip() or rpd < 1:
            raise ValueError("Gemini project quota scope, model and RPD are required")
        if project_rpd is not None and project_rpd < 1:
            raise ValueError("Gemini project daily request limit must be positive")
        now = self._utc(now)
        if project_rpd is not None:
            project_decision = self._reserve_project_capacity(
                quota_scope=quota_scope, project_rpd=project_rpd, now=now
            )
            if not project_decision.allowed:
                return GeminiQuotaDecision(
                    False,
                    "project_rpd_exhausted",
                    project_decision.available_at,
                )
        model_decision = self._reserve_capacity(
            quota_scope=quota_scope, model=model, rpd=rpd, now=now
        )
        if model_decision.allowed:
            return model_decision
        if project_rpd is not None:
            # The global reservation and per-model reservation are one transaction.
            self.session.rollback()
        return model_decision

    def _reserve_project_capacity(
        self, *, quota_scope: str, project_rpd: int, now: datetime
    ) -> GeminiQuotaDecision:
        quota_day = now.astimezone(_PACIFIC_TIME).date()
        table = GeminiProjectQuotaStateModel.__table__
        existing_reserved = int(
            self.session.scalar(
                select(func.coalesce(func.sum(table.c.reserved_requests), 0)).where(
                    table.c.quota_scope == quota_scope,
                    table.c.quota_day == quota_day,
                    table.c.model != _PROJECT_TOTAL_MODEL,
                )
            )
            or 0
        )
        if existing_reserved >= project_rpd:
            return GeminiQuotaDecision(False, "rpd_exhausted", self._next_reset(now))
        return self._reserve_capacity(
            quota_scope=quota_scope,
            model=_PROJECT_TOTAL_MODEL,
            rpd=project_rpd,
            now=now,
            initial_reserved=existing_reserved + 1,
        )

    def _reserve_capacity(
        self,
        *,
        quota_scope: str,
        model: str,
        rpd: int,
        now: datetime,
        initial_reserved: int = 1,
    ) -> GeminiQuotaDecision:
        quota_day = now.astimezone(_PACIFIC_TIME).date()
        reset_at = self._next_reset(now)
        table = GeminiProjectQuotaStateModel.__table__
        statement = self._insert().values(
            quota_scope=quota_scope,
            model=model,
            quota_day=quota_day,
            reserved_requests=initial_reserved,
            blocked_until=None,
            updated_at=now,
        )
        fresh_day = table.c.quota_day != quota_day
        statement = statement.on_conflict_do_update(
            index_elements=("quota_scope", "model"),
            set_={
                "quota_day": case((fresh_day, quota_day), else_=table.c.quota_day),
                "reserved_requests": case(
                    (fresh_day, initial_reserved), else_=table.c.reserved_requests + 1
                ),
                "blocked_until": case(
                    (fresh_day, None), else_=table.c.blocked_until
                ),
                "updated_at": now,
            },
            where=and_(
                or_(table.c.blocked_until.is_(None), table.c.blocked_until <= now),
                or_(fresh_day, table.c.reserved_requests < rpd),
            ),
        ).returning(table.c.reserved_requests)
        reserved = self.session.execute(statement).scalar_one_or_none()
        if reserved is not None:
            return GeminiQuotaDecision(True)
        state = self.session.scalar(
            select(GeminiProjectQuotaStateModel).where(
                GeminiProjectQuotaStateModel.quota_scope == quota_scope,
                GeminiProjectQuotaStateModel.model == model,
            )
        )
        blocked_until = state.blocked_until if state is not None else None
        available_at = (
            max(self._utc(blocked_until), reset_at) if blocked_until else reset_at
        )
        return GeminiQuotaDecision(False, "rpd_exhausted", available_at)

    def block_until(self, *, quota_scope: str, model: str, retry_at: datetime, now: datetime | None = None) -> None:
        now = self._utc(now)
        retry_at = self._utc(retry_at)
        quota_day = now.astimezone(_PACIFIC_TIME).date()
        table = GeminiProjectQuotaStateModel.__table__
        later_block = case(
            (table.c.blocked_until.is_(None), retry_at),
            (table.c.blocked_until < retry_at, retry_at),
            else_=table.c.blocked_until,
        )
        statement = self._insert().values(quota_scope=quota_scope, model=model, quota_day=quota_day, reserved_requests=0, blocked_until=retry_at, updated_at=now).on_conflict_do_update(
            index_elements=("quota_scope", "model"),
            set_={"blocked_until": later_block, "updated_at": now},
        )
        self.session.execute(statement)

    def _insert(self):
        bind = self.session.bind
        return postgresql_insert(GeminiProjectQuotaStateModel) if bind is not None and bind.dialect.name == "postgresql" else sqlite_insert(GeminiProjectQuotaStateModel)

    @staticmethod
    def _utc(value: datetime | None) -> datetime:
        value = value or datetime.now(timezone.utc)
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None or value.utcoffset() is None else value.astimezone(timezone.utc)

    @staticmethod
    def _next_reset(now: datetime) -> datetime:
        local = now.astimezone(_PACIFIC_TIME)
        return datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), tzinfo=_PACIFIC_TIME).astimezone(timezone.utc)
