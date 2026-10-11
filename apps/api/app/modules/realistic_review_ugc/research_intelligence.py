from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import ipaddress
import re
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import RrugcResearchSignalModel


ALLOWED_SOURCES = frozenset({"rss", "github", "youtube", "web"})
VALID_STATUSES = frozenset({"pending", "approved", "rejected"})
MAX_BATCH = 50


class ResearchValidationError(ValueError):
    pass


def _text(value: str, limit: int) -> str:
    return " ".join(re.sub(r"<[^>]*>", " ", re.sub(r"[\x00-\x1f\x7f]+", " ", value)).split())[:limit]


def validate_url(value: str) -> str:
    if not value or len(value) > 2048:
        raise ResearchValidationError("Invalid URL length")
    try:
        url = urlsplit(value)
        host = (url.hostname or "").lower().rstrip(".")
        if (url.scheme != "https" or not host or url.username or url.password
                or url.port not in (None, 443) or host == "localhost"
                or host.endswith((".local", ".internal"))):
            raise ResearchValidationError("Public HTTPS link required")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ResearchValidationError("IP literals are not accepted")
    except ValueError as exc:
        raise ResearchValidationError("Invalid external URL") from exc
    return value


def normalize_keyword(value: str) -> str:
    keyword = _text(value, 150).upper()
    words = keyword.split()
    if not 2 <= len(words) <= 12:
        raise ResearchValidationError("Keyword must contain 2–12 words")
    if not all(re.fullmatch(r"[\w'&\-]+", word, re.UNICODE) for word in words):
        raise ResearchValidationError("Keyword contains invalid characters")
    return keyword


def import_signals(
    session: Session, *, tenant_id: str, user_id: str, signals: list[dict]
) -> dict:
    if not 1 <= len(signals) <= MAX_BATCH:
        raise ResearchValidationError("Import supports 1–50 signals")
    added = duplicates = 0
    for signal in signals:
        source = signal["source"]
        if source not in ALLOWED_SOURCES:
            raise ResearchValidationError("Unsupported research source")
        url = validate_url(signal["url"])
        title = _text(signal["title"], 240)
        if not title:
            raise ResearchValidationError("Research title is required")
        fingerprint = hashlib.sha256((source + "|" + url.lower()).encode()).hexdigest()
        existing = session.scalar(select(RrugcResearchSignalModel.id).where(
            RrugcResearchSignalModel.tenant_id == tenant_id,
            RrugcResearchSignalModel.fingerprint == fingerprint,
        ))
        if existing:
            duplicates += 1
            continue
        session.add(RrugcResearchSignalModel(
            tenant_id=tenant_id, fingerprint=fingerprint, source=source,
            channel=_text(signal.get("channel") or source, 80),
            url=url, title=title, excerpt=_text(signal.get("excerpt") or "", 480),
            status="pending", created_by_user_id=user_id,
            created_at=datetime.now(timezone.utc),
        ))
        session.flush()
        added += 1
    session.commit()
    return {"added": added, "duplicates": duplicates}


def row_dict(row: RrugcResearchSignalModel) -> dict:
    return {
        "id": row.id, "source": row.source, "channel": row.channel,
        "url": row.url, "title": row.title, "excerpt": row.excerpt,
        "status": row.status, "keyword": row.keyword,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
    }


def list_signals(session: Session, *, tenant_id: str, status: str = "pending",
                 limit: int = 25) -> list[dict]:
    if status not in VALID_STATUSES | {"all"} or not 1 <= limit <= 100:
        raise ResearchValidationError("Invalid status or limit")
    statement = select(RrugcResearchSignalModel).where(
        RrugcResearchSignalModel.tenant_id == tenant_id
    )
    if status != "all":
        statement = statement.where(RrugcResearchSignalModel.status == status)
    statement = statement.order_by(RrugcResearchSignalModel.created_at.desc()).limit(limit)
    return [row_dict(row) for row in session.scalars(statement).all()]


def review_signal(
    session: Session, *, tenant_id: str, signal_id: str, user_id: str,
    decision: str, keyword: str | None,
) -> dict:
    if decision not in {"approved", "rejected"}:
        raise ResearchValidationError("Invalid review decision")
    row = session.scalar(select(RrugcResearchSignalModel).where(
        RrugcResearchSignalModel.id == signal_id,
        RrugcResearchSignalModel.tenant_id == tenant_id,
    ).with_for_update())
    if row is None:
        raise LookupError("Research signal not found")
    if row.status != "pending":
        raise ResearchValidationError("Research signal has already been reviewed")
    row.keyword = normalize_keyword(keyword or "") if decision == "approved" else None
    row.status = decision
    row.reviewed_by_user_id = user_id
    row.reviewed_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(row)
    return row_dict(row)


def stage0_export(session: Session, *, tenant_id: str) -> dict:
    rows = session.scalars(select(RrugcResearchSignalModel.keyword).where(
        RrugcResearchSignalModel.tenant_id == tenant_id,
        RrugcResearchSignalModel.status == "approved",
        RrugcResearchSignalModel.keyword.is_not(None),
    ).distinct().limit(MAX_BATCH)).all()
    return {"keywords": rows, "force": False}
