"""Persistent manual feedback shared by all Keyword Scout machines."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session
from .model import RrugcScoutFeedbackModel, RrugcKeywordVolumeModel


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def feedback_targets(row: RrugcKeywordVolumeModel, scope: str):
    if scope in ("keyword", "both"):
        yield ("keyword", row.keyword_normalized, row.keyword)
    if scope in ("pin", "both") and row.source_pin_url:
        from .service import validate_pin_url
        pin_url = validate_pin_url(row.source_pin_url)
        yield ("pin", pin_url, pin_url)


def update_feedback(session: Session, row: RrugcKeywordVolumeModel, action: str, scope: str, user_id: str):
    if scope == "pin" and not row.source_pin_url:
        raise ValueError("This keyword has no Pinterest source Pin.")
    for target_type, target_key, display in feedback_targets(row, scope):
        item = session.scalar(select(RrugcScoutFeedbackModel).where(
            RrugcScoutFeedbackModel.tenant_id == row.tenant_id,
            RrugcScoutFeedbackModel.target_type == target_type,
            RrugcScoutFeedbackModel.target_key == target_key,
        ).with_for_update())
        if item is None:
            item = RrugcScoutFeedbackModel(
                tenant_id=row.tenant_id, target_type=target_type,
                target_key=target_key, display_value=display,
                keyword=row.keyword, source_image_url=row.source_image_url,
                updated_by_user_id=user_id,
            )
            session.add(item)
        item.status = action
        item.keyword = row.keyword
        item.display_value = display
        item.source_image_url = row.source_image_url
        item.updated_by_user_id = user_id
        item.updated_at = now_utc()
        item.processed_at = None
        item.claimed_by_agent_id = None
        item.lease_token = None
        item.lease_expires_at = None


def statuses_for_rows(session: Session, tenant_id: str, rows: list[RrugcKeywordVolumeModel]):
    keys = {(type_, key) for row in rows for type_, key, _ in feedback_targets(row, "both")}
    if not keys:
        return {}
    values = session.scalars(select(RrugcScoutFeedbackModel).where(
        RrugcScoutFeedbackModel.tenant_id == tenant_id,
        RrugcScoutFeedbackModel.target_key.in_([key for _, key in keys]),
    )).all()
    return {(row.target_type, row.target_key): row.status for row in values}


def blocked_targets(session: Session, tenant_id: str):
    rows = session.scalars(select(RrugcScoutFeedbackModel).where(
        RrugcScoutFeedbackModel.tenant_id == tenant_id,
        RrugcScoutFeedbackModel.status == "blocked",
    )).all()
    return {
        "blocked_keywords": [row.target_key for row in rows if row.target_type == "keyword"],
        "blocked_pins": [row.target_key for row in rows if row.target_type == "pin"],
    }


def claim_priority(session: Session, tenant_id: str, agent_id: str):
    now = now_utc()
    pending = session.scalars(select(RrugcScoutFeedbackModel).where(
        RrugcScoutFeedbackModel.tenant_id == tenant_id,
        RrugcScoutFeedbackModel.status == "suggested",
        RrugcScoutFeedbackModel.processed_at.is_(None),
        or_(RrugcScoutFeedbackModel.lease_expires_at.is_(None),
            RrugcScoutFeedbackModel.lease_expires_at < now),
    ).order_by(RrugcScoutFeedbackModel.updated_at.asc()).limit(12)).all()
    for row in pending:
        lease_token = str(uuid4())
        result = session.execute(update(RrugcScoutFeedbackModel).where(
            RrugcScoutFeedbackModel.id == row.id,
            RrugcScoutFeedbackModel.status == "suggested",
            RrugcScoutFeedbackModel.processed_at.is_(None),
            or_(RrugcScoutFeedbackModel.lease_expires_at.is_(None),
                RrugcScoutFeedbackModel.lease_expires_at < now),
        ).values(claimed_by_agent_id=agent_id, lease_token=lease_token,
                 lease_expires_at=now + timedelta(minutes=30)))
        if result.rowcount:
            session.commit()
            return {
                "id": row.id, "lease_token": lease_token,
                "type": row.target_type, "keyword": row.keyword,
                "pin_url": row.display_value if row.target_type == "pin" else None,
                "image_url": row.source_image_url if row.target_type == "pin" else None,
            }
    session.rollback()
    return None


def renew_priority(
    session: Session, tenant_id: str, agent_id: str,
    item_id: str, lease_token: str,
) -> bool:
    """Extend a long-running claim only while its exact lease is still owned."""
    now = now_utc()
    result = session.execute(update(RrugcScoutFeedbackModel).where(
        RrugcScoutFeedbackModel.id == item_id,
        RrugcScoutFeedbackModel.tenant_id == tenant_id,
        RrugcScoutFeedbackModel.claimed_by_agent_id == agent_id,
        RrugcScoutFeedbackModel.lease_token == lease_token,
        RrugcScoutFeedbackModel.status == "suggested",
        RrugcScoutFeedbackModel.processed_at.is_(None),
    ).values(lease_expires_at=now + timedelta(minutes=30)))
    if result.rowcount:
        session.commit()
        return True
    session.rollback()
    return False


def finish_priority(session: Session, tenant_id: str, agent_id: str, item_id: str, lease_token: str, success: bool):
    row = session.scalar(select(RrugcScoutFeedbackModel).where(
        RrugcScoutFeedbackModel.id == item_id,
        RrugcScoutFeedbackModel.tenant_id == tenant_id,
        RrugcScoutFeedbackModel.claimed_by_agent_id == agent_id,
        RrugcScoutFeedbackModel.lease_token == lease_token,
        RrugcScoutFeedbackModel.status == "suggested",
    ).with_for_update())
    if row is None:
        return False
    row.processed_at = now_utc() if success else None
    row.claimed_by_agent_id = None
    row.lease_token = None
    row.lease_expires_at = now_utc() + timedelta(minutes=10) if not success else None
    session.commit()
    return True
