"""Tenant-wide Pinterest query intelligence: durable pool, learning and leases.

Search terms are exclusively about caps/trucker hats. Keyword feedback is a
style signal, not a command to search the bare text. The server leases each
query across all Scout agents to prevent two machines scanning it concurrently.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import re
from uuid import uuid4

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.orm import Session

from .model import (
    RrugcKeywordVolumeModel, RrugcScoutFeedbackModel, RrugcScoutQueryModel,
)


# Curated evergreen exploration lanes: never rely on one repetitive seed.
DISCOVERY_SEEDS = (
    "funny embroidered trucker hat", "retro saying baseball cap",
    "vintage embroidered slogan hat", "western humor trucker hat",
    "minimal text embroidery cap", "sarcastic quote trucker hat",
    "food pun embroidered hat", "outdoors camping saying cap",
    "everyday dad hat embroidery", "cowgirl slogan trucker hat",
    "funny animal quote baseball cap", "hand holding embroidered cap",
    "selfie wearing slogan trucker hat", "workshop custom embroidery hat",
    "retro typography embroidered cap", "southern saying trucker hat",
)
# Generic style discovery is derived from approved phrases without cloning
# branded text or trademark labels into unrelated product searches.
STYLE_SEEDS = (
    "funny saying trucker hat", "humor embroidered baseball cap",
    "vintage lettering trucker cap", "short funny quote embroidered hat",
    "retro embroidered cap aesthetic", "sarcastic saying baseball cap",
    "western style embroidered trucker hat", "minimal stitched phrase cap",
)
PRODUCT_SUFFIXES = ("trucker hat", "embroidered cap", "baseball cap embroidery")
# Weight targets: 35% assisted phrase, 25% learned style, 25% product
# discovery, 15% general exploration. Adapt score within each lane.
LANE_WEIGHTS = {"suggested": 35, "style": 25, "product": 25, "explore": 15}
MIN_REUSE = timedelta(minutes=90)
LEASE_TIME = timedelta(minutes=45)
MAX_QUERY_LENGTH = 240


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip()).casefold()


def clean_phrase(text: str) -> str:
    phrase = re.sub(r"\s+", " ", str(text or "")).strip()[:120]
    phrase = re.sub(r"https?://\S+", "", phrase).strip()
    return phrase if len(phrase.split()) >= 2 else ""


def cap_query(phrase: str, variant: int = 0) -> str:
    phrase = clean_phrase(phrase)
    if not phrase:
        return "funny embroidered trucker hat"
    if re.search(r"\b(?:hat|cap|trucker)\b", phrase, re.I):
        return phrase[:MAX_QUERY_LENGTH]
    return f"{phrase} {PRODUCT_SUFFIXES[variant % len(PRODUCT_SUFFIXES)]}"[:MAX_QUERY_LENGTH]


def style_query(phrase: str) -> str:
    """Expand a chosen phrase into a style rather than endless exact copies."""
    low = normalized(phrase)
    if any(word in low for word in ("funny", "joke", "pun", "sarcas", "hotdog")):
        return "funny humor quote embroidered trucker hat"
    if any(word in low for word in ("cowboy", "western", "cowgirl", "rodeo")):
        return "western vintage embroidered trucker hat"
    if any(word in low for word in ("retro", "vintage", "old school")):
        return "retro lettering saying baseball cap"
    if any(word in low for word in ("dad", "father", "mom", "mama")):
        return "everyday family humor embroidered cap"
    if any(word in low for word in ("fish", "camp", "hiking", "outdoor")):
        return "outdoor adventure embroidered baseball cap"
    return "minimal typography saying trucker hat"


def add_query(
    session: Session, tenant_id: str, query: str, lane: str,
    *, source_keyword: str | None = None,
) -> None:
    query = re.sub(r"\s+", " ", query).strip()[:MAX_QUERY_LENGTH]
    key = normalized(query)
    if not key or lane not in LANE_WEIGHTS or len(query.split()) < 2:
        return
    exists = session.scalar(select(RrugcScoutQueryModel.id).where(
        RrugcScoutQueryModel.tenant_id == tenant_id,
        RrugcScoutQueryModel.query_normalized == key,
    ))
    if exists is not None:
        return
    session.add(RrugcScoutQueryModel(
        tenant_id=tenant_id, query=query, query_normalized=key,
        lane=lane, source_keyword=source_keyword,
    ))
    # Committed after seeding; handle concurrency by checking under writer lock.


def seed_pool(session: Session, tenant_id: str) -> None:
    """Lightweight incremental seeding; bounded for every Scout request."""
    from sqlalchemy.exc import IntegrityError

    blocked = {normalized(target) for target in session.scalars(
        select(RrugcScoutFeedbackModel.target_key).where(
            RrugcScoutFeedbackModel.tenant_id == tenant_id,
            RrugcScoutFeedbackModel.target_type == "keyword",
            RrugcScoutFeedbackModel.status == "blocked",
        )
    ).all()}
    suggested = session.execute(select(
        RrugcScoutFeedbackModel.keyword,
    ).where(
        RrugcScoutFeedbackModel.tenant_id == tenant_id,
        RrugcScoutFeedbackModel.target_type == "keyword",
        RrugcScoutFeedbackModel.status == "suggested",
    ).order_by(RrugcScoutFeedbackModel.updated_at.desc()).limit(200)).scalars().all()
    good = session.scalars(select(RrugcKeywordVolumeModel).where(
        RrugcKeywordVolumeModel.tenant_id == tenant_id,
        or_(RrugcKeywordVolumeModel.favorite.is_(True),
            RrugcKeywordVolumeModel.picked.is_(True)),
    ).order_by(RrugcKeywordVolumeModel.updated_at.desc()).limit(100)).all()
    suggested_keys = [normalized(phrase) for phrase in suggested]
    risk_rows = session.execute(select(
        RrugcKeywordVolumeModel.keyword_normalized,
        RrugcKeywordVolumeModel.trademark_status,
    ).where(
        RrugcKeywordVolumeModel.tenant_id == tenant_id,
        RrugcKeywordVolumeModel.keyword_normalized.in_(suggested_keys),
    )).all()
    risk_by_key = {key: status for key, status in risk_rows}
    risk_by_key.update({normalized(row.keyword): row.trademark_status for row in good})

    candidates: list[tuple[str, str, str | None]] = []
    for i, phrase in enumerate(suggested + [row.keyword for row in good]):
        cleaned = clean_phrase(phrase)
        if not cleaned or normalized(cleaned) in blocked:
            continue
        # Exclude known risky trademarks from exact-product discovery.
        risk = risk_by_key.get(normalized(cleaned))
        if risk not in {"danger", "warning"}:
            candidates.append((cap_query(cleaned, i), "suggested", cleaned))
        candidates.append((style_query(cleaned), "style", cleaned))
    candidates += [(term, "product", None) for term in DISCOVERY_SEEDS]
    candidates += [(term, "explore", None) for term in STYLE_SEEDS]
    # Skip all already-known queries with a single lookup; frequent Scout
    # cycles must not generate hundreds of repeated INSERT checks.
    existing = set(session.scalars(select(
        RrugcScoutQueryModel.query_normalized,
    ).where(RrugcScoutQueryModel.tenant_id == tenant_id)).all())
    for query, lane, source in candidates:
        key = normalized(query)
        if key in existing:
            continue
        try:
            with session.begin_nested():
                add_query(session, tenant_id, query, lane, source_keyword=source)
                session.flush()
            existing.add(key)
        except IntegrityError:
            existing.add(key)
    session.commit()


def _score(row: RrugcScoutQueryModel, now: datetime) -> float:
    scans = max(1, row.scanned_pins)
    novelty = 100 * row.new_keywords / scans
    duplicates = row.duplicate_pins / scans
    dry_penalty = min(30, 8 * row.empty_cycles)
    age = (now - _aware(row.last_searched_at)).total_seconds() / 86400 if row.last_searched_at else 14
    return 30 + min(45, novelty * 3) - 25 * duplicates - dry_penalty + min(22, age * 2)


def query_readiness(
    row: RrugcScoutQueryModel, now: datetime, blocked_keywords: set[str],
) -> str:
    """Single source of truth for lease eligibility and dashboard counters.

    A repeated empty query needs 90, 180, 270 or 360 minutes of rest.
    Dashboard must not count it as ready after just 90 minutes.
    """
    if (row.lane == "suggested" and row.source_keyword
            and normalized(row.source_keyword) in blocked_keywords):
        return "blocked"
    if row.lease_expires_at and _aware(row.lease_expires_at) >= now:
        return "leased"
    reuse = MIN_REUSE * min(4, 1 + max(0, int(row.empty_cycles or 0)))
    if row.last_searched_at and _aware(row.last_searched_at) >= now - reuse:
        return "cooldown"
    return "ready"


def blocked_query_keywords(session: Session, tenant_id: str) -> set[str]:
    return {normalized(key) for key in session.scalars(select(
        RrugcScoutFeedbackModel.target_key,
    ).where(
        RrugcScoutFeedbackModel.tenant_id == tenant_id,
        RrugcScoutFeedbackModel.target_type == "keyword",
        RrugcScoutFeedbackModel.status == "blocked",
    )).all()}


def claim_query(session: Session, tenant_id: str, agent_id: str) -> dict | None:
    now = utcnow()
    seed_pool(session, tenant_id)
    rows = session.scalars(select(RrugcScoutQueryModel).where(
        RrugcScoutQueryModel.tenant_id == tenant_id,
    )).all()
    blocked = blocked_query_keywords(session, tenant_id)
    available = [row for row in rows if query_readiness(row, now, blocked) == "ready"]
    if not available:
        # Don't re-run the same exhausted seed; let caller back off.
        return None
    history = {lane: sum(row.completed_cycles for row in rows if row.lane == lane)
               for lane in LANE_WEIGHTS}
    count = sum(history.values())
    by_lane: dict[str, list[RrugcScoutQueryModel]] = {name: [] for name in LANE_WEIGHTS}
    for row in available:
        by_lane[row.lane].append(row)
    # Fair-share deficit prevents a high-volume lane starving other lanes.
    lanes = sorted(
        (lane for lane in LANE_WEIGHTS if by_lane[lane]),
        key=lambda lane: (count + 1) * LANE_WEIGHTS[lane] / 100 - history[lane],
        reverse=True,
    )
    for lane in lanes:
        ordered = sorted(by_lane[lane], key=lambda row: (_score(row, now), -(row.completed_cycles)), reverse=True)
        for row in ordered[:25]:
            token = str(uuid4())
            change = session.execute(update(RrugcScoutQueryModel).where(
                RrugcScoutQueryModel.id == row.id,
                or_(RrugcScoutQueryModel.lease_expires_at.is_(None),
                    RrugcScoutQueryModel.lease_expires_at < now),
            ).values(lease_token=token, claimed_by_agent_id=agent_id,
                     lease_expires_at=now + LEASE_TIME).execution_options(synchronize_session=False))
            if change.rowcount:
                session.commit()
                return {"id": row.id, "query": row.query, "lane": lane,
                        "lease_token": token, "source_keyword": row.source_keyword}
    session.rollback()
    return None


def renew_query(
    session: Session, tenant_id: str, agent_id: str, item_id: str, lease_token: str,
) -> bool:
    now = utcnow()
    result = session.execute(update(RrugcScoutQueryModel).where(
        RrugcScoutQueryModel.id == item_id,
        RrugcScoutQueryModel.tenant_id == tenant_id,
        RrugcScoutQueryModel.claimed_by_agent_id == agent_id,
        RrugcScoutQueryModel.lease_token == lease_token,
        RrugcScoutQueryModel.lease_expires_at > now,
    ).values(lease_expires_at=now + LEASE_TIME).execution_options(synchronize_session=False))
    if result.rowcount:
        session.commit()
        return True
    session.rollback()
    return False


def finish_query(
    session: Session, tenant_id: str, agent_id: str, item_id: str,
    lease_token: str, *, success: bool, scanned_pins: int,
    found_quotes: int, new_keywords: int, duplicate_pins: int,
    retryable: bool = False,
) -> bool:
    row = session.scalar(select(RrugcScoutQueryModel).where(
        RrugcScoutQueryModel.id == item_id,
        RrugcScoutQueryModel.tenant_id == tenant_id,
        RrugcScoutQueryModel.claimed_by_agent_id == agent_id,
        RrugcScoutQueryModel.lease_token == lease_token,
        RrugcScoutQueryModel.lease_expires_at > utcnow(),
    ).with_for_update())
    if row is None:
        return False
    now = utcnow()
    if success:
        row.completed_cycles += 1
        row.scanned_pins += scanned_pins
        row.found_quotes += found_quotes
        row.new_keywords += new_keywords
        row.duplicate_pins += duplicate_pins
        row.empty_cycles = row.empty_cycles + 1 if new_keywords == 0 else 0
        row.last_searched_at = now
    elif not retryable:
        row.failed_cycles += 1
        row.last_searched_at = now
        row.empty_cycles += 1
    # A Gemini-capacity pause, Pinterest rate-limit or browser access gate
    # is not evidence that this query is low quality. Release the lease
    # without modifying learning counters/cooldown.
    row.claimed_by_agent_id = None
    row.lease_token = None
    row.lease_expires_at = None
    row.updated_at = now
    session.commit()
    return True


def intelligence_summary(session: Session, tenant_id: str) -> dict:
    rows = session.scalars(select(RrugcScoutQueryModel).where(
        RrugcScoutQueryModel.tenant_id == tenant_id,
    ).order_by(RrugcScoutQueryModel.completed_cycles.desc())).all()
    now = utcnow()
    data = sorted(rows, key=lambda row: _aware(row.last_searched_at) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return {
        "total_queries": len(rows),
        "cycles_completed": sum(row.completed_cycles for row in rows),
        "new_keywords": sum(row.new_keywords for row in rows),
        "duplicate_pins": sum(row.duplicate_pins for row in rows),
        "active_leases": sum(bool(row.lease_expires_at and _aware(row.lease_expires_at) > now) for row in rows),
        "lanes": [
            {"lane": lane, "queries": sum(row.lane == lane for row in rows),
             "cycles": sum(row.completed_cycles for row in rows if row.lane == lane)}
            for lane in LANE_WEIGHTS
        ],
        "recent": [
            {"query": row.query, "lane": row.lane,
             "cycles": row.completed_cycles, "new_keywords": row.new_keywords,
             "scanned_pins": row.scanned_pins, "duplicate_pins": row.duplicate_pins,
             "last_searched_at": row.last_searched_at.isoformat() if row.last_searched_at else None}
            for row in data[:12]
        ],
    }
