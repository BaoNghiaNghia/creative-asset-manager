from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, case, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.modules.ai_governance.model import AiModelRateLimitStateModel


def configured_model_rates(
    settings: Any, provider: str, requested_model: str
) -> tuple[tuple[str, int], ...]:
    """Return the ordered shared start gates for a provider request."""
    if provider == "gemini":
        limits = settings.gemini_model_limits
        return tuple(
            (
                model,
                settings.ai_model_rpm(provider, model) or limits[model].rpm,
            )
            for model in settings.gemini_model_pool
        )
    rpm = settings.ai_model_rpm(provider, requested_model)
    return ((requested_model, rpm),) if rpm is not None else ()


@dataclass(frozen=True, slots=True)
class ModelRateLimitDecision:
    allowed: bool
    next_eligible_at: datetime
    delay_seconds: float


class AiModelRateLimitRepository:
    """Tenant/provider/model start limiter backed by the primary database."""

    def __init__(self, session: Session):
        self.session = session

    def reserve_start(
        self,
        *,
        tenant_id: str,
        provider: str,
        model: str,
        rpm: int,
        minimum_interval_seconds: float,
        now: datetime | None = None,
    ) -> ModelRateLimitDecision:
        if rpm <= 0:
            raise ValueError("rpm must be positive")
        if minimum_interval_seconds <= 0:
            raise ValueError("minimum_interval_seconds must be positive")
        now = self._utc(now)
        delay_seconds = max(float(minimum_interval_seconds), 60.0 / rpm)
        next_eligible_at = now + timedelta(seconds=delay_seconds)
        statement = self._insert().values(
            tenant_id=tenant_id,
            provider=provider,
            model=model,
            last_started_at=now,
            next_eligible_at=next_eligible_at,
            blocked_until=None,
            updated_at=now,
        )
        table = AiModelRateLimitStateModel.__table__
        statement = statement.on_conflict_do_update(
            index_elements=("tenant_id", "provider", "model"),
            set_={
                "last_started_at": now,
                "next_eligible_at": next_eligible_at,
                "updated_at": now,
            },
            where=and_(
                table.c.next_eligible_at <= now,
                or_(table.c.blocked_until.is_(None), table.c.blocked_until <= now),
            ),
        ).returning(table.c.next_eligible_at)
        updated = self.session.execute(statement).scalar_one_or_none()
        if updated is not None:
            return ModelRateLimitDecision(True, self._utc(updated), delay_seconds)

        state = self.session.scalar(
            select(AiModelRateLimitStateModel).where(
                AiModelRateLimitStateModel.tenant_id == tenant_id,
                AiModelRateLimitStateModel.provider == provider,
                AiModelRateLimitStateModel.model == model,
            )
        )
        if state is None:
            return ModelRateLimitDecision(
                False, now + timedelta(seconds=delay_seconds), delay_seconds
            )
        retry_at = max(
            self._utc(state.next_eligible_at),
            self._utc(state.blocked_until) if state.blocked_until else now,
        )
        return ModelRateLimitDecision(False, retry_at, delay_seconds)

    def next_start(
        self,
        *,
        tenant_id: str,
        provider: str,
        model: str,
        rpm: int,
        minimum_interval_seconds: float,
        now: datetime | None = None,
    ) -> ModelRateLimitDecision:
        """Read the current start slot without extending or blocking it."""
        if rpm <= 0:
            raise ValueError("rpm must be positive")
        if minimum_interval_seconds <= 0:
            raise ValueError("minimum_interval_seconds must be positive")
        now = self._utc(now)
        delay_seconds = max(float(minimum_interval_seconds), 60.0 / rpm)
        state = self.session.scalar(
            select(AiModelRateLimitStateModel).where(
                AiModelRateLimitStateModel.tenant_id == tenant_id,
                AiModelRateLimitStateModel.provider == provider,
                AiModelRateLimitStateModel.model == model,
            )
        )
        if state is None:
            return ModelRateLimitDecision(True, now, delay_seconds)
        retry_at = max(
            self._utc(state.next_eligible_at),
            self._utc(state.blocked_until) if state.blocked_until else now,
        )
        return ModelRateLimitDecision(retry_at <= now, retry_at, delay_seconds)

    def block_until(
        self,
        *,
        tenant_id: str,
        provider: str,
        model: str,
        retry_at: datetime,
        now: datetime | None = None,
    ) -> None:
        """Persist only provider-confirmed cooldown/quota availability."""
        retry_at = self._utc(retry_at)
        now = self._utc(now)
        table = AiModelRateLimitStateModel.__table__
        later_block = case(
            (table.c.blocked_until.is_(None), retry_at),
            (table.c.blocked_until < retry_at, retry_at),
            else_=table.c.blocked_until,
        )
        statement = self._insert().values(
            tenant_id=tenant_id,
            provider=provider,
            model=model,
            last_started_at=None,
            next_eligible_at=retry_at,
            blocked_until=retry_at,
            updated_at=now,
        )
        statement = statement.on_conflict_do_update(
            index_elements=("tenant_id", "provider", "model"),
            set_={
                "blocked_until": later_block,
                "next_eligible_at": case(
                    (table.c.next_eligible_at < retry_at, retry_at),
                    else_=table.c.next_eligible_at,
                ),
                "updated_at": now,
            },
        )
        self.session.execute(statement)

    def _insert(self):
        bind = self.session.bind
        if bind is not None and bind.dialect.name == "postgresql":
            return postgresql_insert(AiModelRateLimitStateModel)
        return sqlite_insert(AiModelRateLimitStateModel)

    @staticmethod
    def _utc(value: datetime | None) -> datetime:
        value = value or datetime.now(timezone.utc)
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
