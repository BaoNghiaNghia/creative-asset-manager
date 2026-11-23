"""Tenant-scoped, lease-aware Muse command discovery.

Discovery is read-only: claiming still requires the atomic command claim API.
In particular an active or malformed lease must never hide later pending work
by consuming the database-side LIMIT before eligibility is checked.
"""
from __future__ import annotations

from datetime import datetime
from collections.abc import Iterable

from sqlalchemy import and_, or_, select

from .model import CamMuseCommand


def claimable_commands_query(
    tenant_id: str,
    now: datetime,
    *,
    actions: Iterable[str] | None = None,
):
    """Oldest available first; exclude active and NULL-expiry claimed leases."""
    claimable = or_(
        CamMuseCommand.status == "pending",
        and_(
            CamMuseCommand.status == "claimed",
            CamMuseCommand.lease_expires_at.is_not(None),
            CamMuseCommand.lease_expires_at <= now,
        ),
    )
    query = select(CamMuseCommand).where(
        CamMuseCommand.tenant_id == tenant_id,
        CamMuseCommand.attempts < 5,
        claimable,
    )
    if actions is not None:
        query = query.where(CamMuseCommand.action.in_(tuple(actions)))
    return query.order_by(
        CamMuseCommand.created_at.asc(),
        CamMuseCommand.command_id.asc(),
    )
