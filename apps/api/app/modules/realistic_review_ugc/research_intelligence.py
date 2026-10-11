from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import ipaddress
import re
from uuid import uuid4
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import RrugcResearchSignalModel, RrugcKeywordVolumeModel
from app.modules.realistic_review_ugc.keyword_volume import (
    KEYWORD_VOLUME_PENDING_PROVIDER, KeywordVolumeError,
    normalize_keyword as normalize_stage0_keyword,
)


ALLOWED_SOURCES = frozenset({"rss", "github", "youtube", "web", "pinterest", "etsy", "amazon"})
MARKETPLACE_HOSTS = {
    "pinterest": frozenset({"pinterest.com", "www.pinterest.com"}),
    "etsy": frozenset({"etsy.com", "www.etsy.com"}),
    "amazon": frozenset({"amazon.com", "www.amazon.com"}),
}
MARKETPLACE_ITEM_PATHS = {
    "pinterest": re.compile(r"^/pin/(\d+)(?:/|$)", re.I),
    "etsy": re.compile(r"^/listing/(\d+)(?:/|$)", re.I),
    "amazon": re.compile(r"^/(?:dp|gp/product)/([a-z0-9]{10})(?:/|$)", re.I),
}
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


def canonical_marketplace_url(source: str, raw_url: str) -> str:
    """Normalize an item link, never claim that its contents were API-verified.

    Strict official host + product/Pin ID matching prevents brand-domain spoofing
    and collapses referral parameters into one tenant-scoped source identity.
    """
    checked = validate_url(raw_url)
    if source not in MARKETPLACE_HOSTS:
        return checked
    url = urlsplit(checked)
    host = (url.hostname or "").lower().rstrip(".")
    if host not in MARKETPLACE_HOSTS[source]:
        raise ResearchValidationError("Source and marketplace hostname do not match")
    match = MARKETPLACE_ITEM_PATHS[source].match(url.path)
    if not match:
        raise ResearchValidationError("A canonical marketplace item link is required")
    item_id = match.group(1).upper() if source == "amazon" else match.group(1)
    paths = {"pinterest": f"/pin/{item_id}", "etsy": f"/listing/{item_id}",
             "amazon": f"/dp/{item_id}"}
    return "https://www." + source + ".com" + paths[source]


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
        url = canonical_marketplace_url(source, signal["url"])
        title = _text(signal["title"], 240)
        if not title:
            raise ResearchValidationError("Research title is required")
        fingerprint = hashlib.sha256((source + "|" + url.lower()).encode()).hexdigest()
        # The database uniqueness constraint, not a read-then-insert check,
        # arbitrates concurrent Scout deliveries. Both SQLite tests and
        # production PostgreSQL support ON CONFLICT DO NOTHING.
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            statement = postgresql_insert(RrugcResearchSignalModel)
        elif dialect == "sqlite":
            statement = sqlite_insert(RrugcResearchSignalModel)
        else:
            raise ResearchValidationError("Unsupported research database dialect")
        inserted = session.execute(statement.values(
            id=str(uuid4()),
            tenant_id=tenant_id, fingerprint=fingerprint, source=source,
            channel=_text(signal.get("channel") or source, 80),
            url=url, title=title, excerpt=_text(signal.get("excerpt") or "", 480),
            status="pending", created_by_user_id=user_id,
            created_at=datetime.now(timezone.utc),
        ).on_conflict_do_nothing(index_elements=["tenant_id", "fingerprint"]))
        if inserted.rowcount == 1:
            added += 1
        else:
            duplicates += 1
    session.commit()
    return {"added": added, "duplicates": duplicates}


def row_dict(row: RrugcResearchSignalModel) -> dict:
    return {
        "id": row.id, "source": row.source, "platform": row.source if row.source in MARKETPLACE_HOSTS else "other", "channel": row.channel,
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


def _insert_stage0_pending_keyword(
    session: Session, tenant_id: str, row: RrugcResearchSignalModel,
) -> bool:
    """Idempotent Stage 0 promotion without calling AEBrowse.

    Concurrent or repeated approvals never reset existing provider metrics,
    user picks, search volume or trademark checks.
    """
    if not row.keyword:
        return False
    try:
        clean, normalized = normalize_stage0_keyword(row.keyword or "")
    except KeywordVolumeError as exc:
        raise ResearchValidationError(str(exc)) from exc
    values = dict(
        tenant_id=tenant_id, keyword=clean, keyword_normalized=normalized,
        provider=KEYWORD_VOLUME_PENDING_PROVIDER, request_count=0,
        source_pin_url=row.url if row.source == "pinterest" else None,
        provider_raw_json={
            "research_signal_id": row.id,
            "research_source": row.source,
            "research_source_url": row.url,
            "research_unverified": True,
        },
    )
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(RrugcKeywordVolumeModel)
    elif dialect == "sqlite":
        statement = sqlite_insert(RrugcKeywordVolumeModel)
    else:
        raise ResearchValidationError("Unsupported research database dialect")
    inserted = session.execute(statement.values(**values).on_conflict_do_nothing(
        index_elements=["tenant_id", "keyword_normalized"]
    ))
    return inserted.rowcount == 1


def apply_approved_to_stage0(session: Session, *, tenant_id: str) -> dict:
    """Repair earlier approvals that only reached the Research Inbox export."""
    rows = session.scalars(select(RrugcResearchSignalModel).where(
        RrugcResearchSignalModel.tenant_id == tenant_id,
        RrugcResearchSignalModel.status == "approved",
        RrugcResearchSignalModel.keyword.is_not(None),
    ).order_by(RrugcResearchSignalModel.created_at.asc()).limit(1000)).all()
    added = sum(1 for row in rows if _insert_stage0_pending_keyword(session, tenant_id, row))
    session.commit()
    return {"added": added, "existing": len(rows) - added, "checked": len(rows)}


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
    # Only a human-approved quote becomes a Stage 0 keyword. AEBrowse is not
    # called here, and an unknown volume is never presented as verified.
    if row.keyword:
        _insert_stage0_pending_keyword(session, tenant_id, row)
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
