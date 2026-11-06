"""Authenticated, idempotent Etsy Spy replication from local Windows SQLite."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .model import (
    EtsySpyAgent, EtsySpyBatch, EtsySpySession, EtsySpyKeyword,
    EtsySpyListing, EtsySpyTrademark, EtsySpyLog, utcnow,
)

MAX_BATCH_BYTES = 2 * 1024 * 1024
MAX_BATCH_ROWS = 200
MAX_SECTION_ROWS = 200
SECTIONS = ("sessions", "keywords", "listings", "trademarks", "logs")
_SECRET_RE = re.compile(
    r"(?i)(bearer\s+)[^\s,;]+|"
    r"\b(?:access[_-]?token|refresh[_-]?token|cookie|password|api[_-]?key|secret)\s*[:=]\s*[^\s,;]+"
)
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def text(value: Any, maximum: int = 200, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise HTTPException(422, "Missing required record identity")
        return None
    if not isinstance(value, (str, int)):
        raise HTTPException(422, "Invalid field type")
    result = str(value).strip()
    if required and not result:
        raise HTTPException(422, "Missing required record identity")
    if len(result) > maximum:
        raise HTTPException(422, "Record field exceeds maximum length")
    return result or None


def moment(value: Any) -> datetime | None:
    if not value:
        return None
    if not isinstance(value, str) or len(value) > 45:
        raise HTTPException(422, "Invalid timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)
    except ValueError as exc:
        raise HTTPException(422, "Invalid timestamp") from exc


def integer(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise HTTPException(422, "Invalid number")
    try:
        number = int(value)
        if number > 2_000_000_000 or number < -2_000_000_000:
            raise ValueError()
        return number
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "Invalid number") from exc


def floating(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
        if result != result or abs(result) > 1e9:
            raise ValueError()
        return result
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "Invalid number") from exc


def safe_log(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(422, "Invalid log text")
    clean = _SECRET_RE.sub("[REDACTED]", value)
    clean = _EMAIL_RE.sub("[EMAIL]", clean)
    clean = re.sub(r"https?://[^\\s\\x00-\\x1f<>\\\"']+", "[URL]", clean, flags=re.I)
    return clean[:limit]


def verified_url(value: Any, hosts: set[str] | None = None) -> str | None:
    url = text(value, 1000)
    if not url:
        return None
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(422, "Invalid source URL")
    if hosts is not None and parsed.hostname.lower() not in hosts:
        raise HTTPException(422, "Unexpected source URL")
    return url


def create_agent(db: Session, tenant_id: str, name: str) -> tuple[EtsySpyAgent, str]:
    secret = secrets.token_urlsafe(48)
    agent = EtsySpyAgent(
        id=str(uuid4()), tenant_id=tenant_id, name=text(name, 100, required=True),
        token_hash=hashlib.sha256(secret.encode()).hexdigest(),
        active=True, created_at=utcnow(),
    )
    db.add(agent)
    db.commit()
    return agent, secret


def authenticate_agent(db: Session, agent_id: str, authorization: str | None) -> EtsySpyAgent:
    agent = db.get(EtsySpyAgent, agent_id)
    match = re.fullmatch(r"Bearer ([A-Za-z0-9_-]{64,128})", authorization or "")
    candidate = hashlib.sha256((match.group(1) if match else "").encode()).hexdigest()
    expected = agent.token_hash if agent is not None else "0" * 64
    valid = bool(agent is not None and agent.active and match and
                 hmac.compare_digest(candidate, expected))
    if not valid:
        raise HTTPException(401, "Invalid or revoked Etsy Spy agent credential")
    return agent


def _normalized_phrase(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _matches_title(mark: dict[str, Any], title: str) -> bool:
    phrase = _normalized_phrase(str(mark.get("markText") or ""))
    actual = _normalized_phrase(title)
    return bool(phrase and (" " + phrase + " ") in (" " + actual + " "))


def _payload_fields(section: str, row: dict, tenant: str, agent: str) -> tuple[type, dict[str, Any], dict[str, Any]]:
    session_id = text(row.get("sessionId"), 150, required=section in {"sessions", "keywords", "listings"})
    base = {"tenant_id": tenant, "agent_id": agent}
    if section == "sessions":
        identity = {**base, "source_session_id": session_id}
        values = {
            "mode": text(row.get("mode"), 40),
            "status": text(row.get("status"), 50),
            "phase": text(row.get("phase"), 80),
            "keyword": text(row.get("keyword"), 220),
            "revision": integer(row.get("revision")) or 0,
            "started_at": moment(row.get("startedAt")),
            "updated_at": moment(row.get("updatedAt")),
        }
        return EtsySpySession, identity, values
    if section == "keywords":
        identity = {**base, "source_session_id": session_id,
                    "node_id": text(row.get("id"), 150, required=True)}
        evidence = row.get("evidence")
        if evidence is not None and not isinstance(evidence, (list, dict)):
            raise HTTPException(422, "Invalid keyword evidence")
        values = {
            "keyword": text(row.get("keyword"), 220, required=True),
            "parent_id": text(row.get("parentId"), 150),
            "depth": integer(row.get("depth")),
            "priority": floating(row.get("priority")),
            "status": text(row.get("status"), 50),
            "reason": text(row.get("reason"), 1000),
            "evidence_json": evidence,
            "source_url": verified_url(row.get("sourceUrl")),
            "matched_count": integer(row.get("matchedCount")),
            "updated_at": moment(row.get("updatedAt")),
        }
        return EtsySpyKeyword, identity, values
    if section == "listings":
        title = text(row.get("pageTitle"), 700, required=True)
        if re.search(r"\bcustom\b", title, flags=re.I):
            raise HTTPException(422, "Custom titles are not eligible listings")
        source_kind = text(row.get("sourceKind"), 20, required=True)
        if source_kind not in {"search", "shop"}:
            raise HTTPException(422, "Invalid listing source kind")
        identity = {**base, "source_session_id": session_id,
                    "keyword": text(row.get("keyword"), 220, required=True),
                    "source_kind": source_kind,
                    "listing_key": text(row.get("listingKey"), 150, required=True)}
        tm_key = text(row.get("tmKey"), 64)
        if tm_key and not re.fullmatch(r"[0-9a-f]{64}", tm_key):
            raise HTTPException(422, "Invalid TM identity")
        metadata = {
            "sourceKinds": [text(x, 20) for x in (row.get("sourceKinds") or [])[:20]],
            "keywordAliases": [text(x, 220) for x in (row.get("keywordAliases") or [])[:40]],
            "occurrences": integer(row.get("occurrences")),
            "keywordCount": integer(row.get("keywordCount")),
            "sessionCount": integer(row.get("sessionCount")),
        }
        values = {
            "title": title, "url": verified_url(row.get("url"), {"etsy.com", "www.etsy.com"}),
            "badge": text(row.get("badge"), 70),
            "search_badge": text(row.get("searchBadge"), 70),
            "source_shop_url": verified_url(row.get("sourceShopUrl"), {"etsy.com", "www.etsy.com"}),
            "shop_position": integer(row.get("shopPosition")),
            "search_position": integer(row.get("searchPosition")),
            "bought24h": integer(row.get("bought24h")),
            "views24h": integer(row.get("views24h")),
            "in_carts": integer(row.get("inCarts")),
            "bestseller": row.get("bestseller") if isinstance(row.get("bestseller"), bool) else
                          bool(row.get("bestseller")) if row.get("bestseller") in (0, 1) else None,
            "demand_score": floating(row.get("demandScore")),
            "tm_key": tm_key, "metadata_json": metadata,
            "observed_at": moment(row.get("observedAt")),
            "updated_at": moment(row.get("updatedAt")),
        }
        return EtsySpyListing, identity, values
    if section == "trademarks":
        matched = row.get("matched") or []
        if not isinstance(matched, list) or len(matched) > 70:
            raise HTTPException(422, "Invalid trademark matches")
        identity = {**base, "check_id": text(row.get("checkId"), 96, required=True)}
        kind = text(row.get("kind"), 30, required=True)
        title = text(row.get("title"), 700)
        if kind not in {"product", "keyword", "term"}:
            raise HTTPException(422, "Invalid trademark type")
        if kind == "product":
            if not title:
                raise HTTPException(422, "Product trademark requires actual listing title")
            # Never mark product LIVE based solely on its search keyword.
            matched = [mark for mark in matched if isinstance(mark, dict) and
                       _matches_title(mark, title)]
            live = sum(str(mark.get("liveStatus") or "").upper() == "LIVE" for mark in matched)
            dead = sum(str(mark.get("liveStatus") or "").upper() == "DEAD" for mark in matched)
            unknown = len(matched) - live - dead
        else:
            live = integer(row.get("liveCount")) or 0
            dead = integer(row.get("deadCount")) or 0
            unknown = integer(row.get("unknownCount")) or 0
        values = {
            "kind": kind,
            "keyword": text(row.get("keyword"), 220),
            "title": title,
            "status": text(row.get("status"), 35),
            "risk": text(row.get("risk"), 55),
            "live_count": live, "dead_count": dead, "unknown_count": unknown,
            "matched_json": matched,
            "source": text(row.get("source"), 80),
            "checked_at": moment(row.get("checkedAt")),
            "source_as_of": moment(row.get("sourceAsOf")),
        }
        return EtsySpyTrademark, identity, values
    if section == "logs":
        identity = {**base, "event_id": text(row.get("eventId"), 180, required=True)}
        event_at = moment(row.get("at"))
        if event_at is None:
            raise HTTPException(422, "Log timestamp is required")
        values = {
            "source_session_id": text(row.get("sessionId"), 150),
            "event_at": event_at,
            "level": text(row.get("level"), 25),
            "source": text(row.get("source"), 80),
            "category": text(row.get("category"), 80),
            "action": text(row.get("action"), 100),
            "phase": text(row.get("phase"), 100),
            "keyword": text(row.get("keyword"), 220),
            "message": safe_log(row.get("message"), 1200),
            "stack": safe_log(row.get("stack"), 2500),
        }
        return EtsySpyLog, identity, values
    raise HTTPException(422, "Unexpected record section")


def _should_update(current: Any, incoming: dict[str, Any], section: str) -> bool:
    if section == "logs":
        return False
    if section == "sessions":
        if incoming["revision"] != current.revision:
            return incoming["revision"] > current.revision
        previous = current.updated_at
        updated = incoming.get("updated_at")
        if previous is not None and previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)
        return previous is None or (updated is not None and updated >= previous)
    if section == "trademarks":
        older = current.checked_at
        newer = incoming.get("checked_at")
        if older is not None and older.tzinfo is None:
            older = older.replace(tzinfo=timezone.utc)
        return not older or (newer is not None and newer >= older)
    older = getattr(current, "updated_at", None)
    newer = incoming.get("updated_at")
    if older is not None and older.tzinfo is None:
        older = older.replace(tzinfo=timezone.utc)
    return not older or (newer is not None and newer >= older)


def sync_batch(db: Session, agent: EtsySpyAgent, body: bytes) -> dict[str, Any]:
    if len(body) > MAX_BATCH_BYTES:
        raise HTTPException(413, "Sync batch too large")
    try:
        packet = json.loads(body)
    except (UnicodeError, ValueError) as exc:
        raise HTTPException(422, "Invalid JSON batch") from exc
    if not isinstance(packet, dict) or packet.get("schema_version") != 1:
        raise HTTPException(422, "Unknown Etsy Spy sync schema")
    batch_id = text(packet.get("batch_id"), 96, required=True)
    cursor = text(packet.get("cursor"), 180)
    records = packet.get("records")
    if not isinstance(records, dict) or set(records) - set(SECTIONS):
        raise HTTPException(422, "Invalid batch records")
    if any(not isinstance(value, list) or len(value) > MAX_SECTION_ROWS for value in records.values()):
        raise HTTPException(422, "Invalid batch size")
    total = sum(len(v) for v in records.values())
    if total > MAX_BATCH_ROWS:
        raise HTTPException(422, "Too many records")
    if any(not isinstance(row, dict) for values in records.values() for row in values):
        raise HTTPException(422, "Invalid record")
    payload_sha = hashlib.sha256(body).hexdigest()
    existing = db.scalar(select(EtsySpyBatch).where(
        EtsySpyBatch.tenant_id == agent.tenant_id,
        EtsySpyBatch.agent_id == agent.id, EtsySpyBatch.batch_id == batch_id,
    ))
    if existing is not None:
        if existing.payload_sha256 != payload_sha:
            raise HTTPException(409, "Batch ID already acknowledged with another payload")
        return {"ok": True, "replayed": True, "batch_id": batch_id,
                "cursor": existing.cursor, "rows": existing.row_count}
    validated = []
    for section in SECTIONS:
        for row in records.get(section, []):
            validated.append((section, *_payload_fields(section, row, agent.tenant_id, agent.id)))
    # All rows and the batch acknowledgement commit together. Retries cannot
    # create duplicate observations or incorrectly clear older snapshots.
    for section, model, identity, values in validated:
        row = db.get(model, tuple(identity.values()))
        if row is None:
            db.add(model(**identity, **values))
        elif _should_update(row, values, section):
            for key, value in values.items():
                setattr(row, key, value)
    db.add(EtsySpyBatch(
        id=str(uuid4()), tenant_id=agent.tenant_id, agent_id=agent.id,
        batch_id=batch_id, payload_sha256=payload_sha,
        cursor=cursor, row_count=total, received_at=utcnow(),
    ))
    agent.last_seen_at = utcnow()
    agent.last_synced_at = utcnow()
    agent.last_cursor = cursor or agent.last_cursor
    agent.last_error = None
    db.commit()
    return {"ok": True, "replayed": False, "batch_id": batch_id,
            "cursor": cursor, "rows": total}


def verify_agent_and_batch(db: Session, agent_id: str, authorization: str | None) -> EtsySpyAgent:
    return authenticate_agent(db, agent_id, authorization)
