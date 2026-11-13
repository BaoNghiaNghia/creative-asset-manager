"""Agent-authenticated Etsy Spy review decisions with durable idempotent receipts.

Only the scoped Windows Etsy Spy agent may create a decision for a listing
already qualified in CAM's tenant projection. The review receipt and the
existing Pick/Later/Win effects commit in ONE database transaction.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .model import (
    EtsySpyAgent as Agent, EtsySpyListing as Listing,
    EtsySpyPicked as Picked, EtsySpyLaterSeed as LaterSeed,
    EtsySpyWinKeyword as WinKeyword, EtsySpyReviewReceipt as Receipt,
    utcnow,
)
from .later_queue import refresh_later_matches, seed_analysis
from .extraction import create_extraction_on_pick
from .tiktok_skill import queue_on_pick as queue_tiktok_skill_on_pick


ACTIONS = frozenset({"pick", "later", "win_keyword"})
REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{12,80}$")
LISTING_KEY = re.compile(r"^[0-9]{5,20}$")
EVIDENCE = re.compile(r"^[a-f0-9]{32}$")


def _payload(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "requestId", "listingKey", "action", "keyword", "evidenceHash"
    }:
        raise HTTPException(422, "Invalid review intent envelope")
    request_id, listing_id, action, evidence_hash = (
        value.get("requestId"), value.get("listingKey"),
        value.get("action"), value.get("evidenceHash")
    )
    if (not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id)
            or not isinstance(listing_id, str) or not LISTING_KEY.fullmatch(listing_id)
            or not isinstance(action, str) or action not in ACTIONS
            or not isinstance(evidence_hash, str) or not EVIDENCE.fullmatch(evidence_hash)):
        raise HTTPException(422, "Invalid review intent identifier or action")
    raw = value.get("keyword")
    if not isinstance(raw, str) or len(raw) > 220:
        raise HTTPException(422, "Invalid review intent keyword")
    keyword = " ".join(raw.split())
    if not 2 <= len(keyword) <= 220:
        raise HTTPException(422, "Invalid review intent keyword")
    return {**value, "keyword": keyword}


def _receipt_view(row: Receipt) -> dict:
    return {
        "ok": True, "requestId": row.request_id,
        "listingKey": row.listing_key, "action": row.action,
        "keyword": row.keyword, "status": "cam_confirmed",
        "outcome": row.outcome, "confirmedAt": row.confirmed_at.isoformat(),
    }


def get_review_receipt(db: Session, agent: Agent, request_id: str) -> dict:
    if not REQUEST_ID.fullmatch(request_id):
        raise HTTPException(422, "Invalid review intent ID")
    row = db.get(Receipt, (agent.tenant_id, agent.id, request_id))
    if row is None:
        raise HTTPException(404, "Review intent receipt not found")
    return _receipt_view(row)


def apply_review_intent(
    db: Session, agent: Agent, payload: Any, *,
    qualified, blacklisted, auto_later, later_seed_limit: int = 500,
    win_keyword_limit: int = 1000,
) -> dict:
    data = _payload(payload)
    tenant, listing_id, action, keyword = (
        agent.tenant_id, data["listingKey"], data["action"], data["keyword"]
    )
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    identity = (tenant, agent.id, data["requestId"])
    prior = db.get(Receipt, identity)
    if prior is not None:
        if prior.payload_sha256 != fingerprint:
            raise HTTPException(409, "Review request ID reused with a different payload")
        return _receipt_view(prior)

    # Lock one tenant/listing row on PostgreSQL so competing Pick/Later writes
    # are ordered even if different agent credentials target the same ID.
    db.scalar(select(Listing).where(
        Listing.tenant_id == tenant, Listing.listing_key == listing_id,
    ).order_by(Listing.agent_id, Listing.source_session_id).limit(1).with_for_update())

    # Unlike the browser, the agent cannot assert Eligibility. CAM verifies its
    # own CURRENT observation from that SAME agent with matching source keyword.
    source = db.scalar(select(Listing).where(
        Listing.tenant_id == tenant,
        Listing.agent_id == agent.id, Listing.listing_key == listing_id,
        Listing.is_current.is_(True),
        func.lower(func.trim(Listing.keyword)) == keyword.casefold(),
        ~Listing.title.ilike("%custom%"),
        blacklisted(tenant), qualified(),
    ).order_by(Listing.observed_at.desc(), Listing.updated_at.desc()).limit(1))
    if source is None:
        raise HTTPException(409, "CAM eligible evidence not yet synchronized")

    existing_pick = db.get(Picked, (tenant, listing_id))
    existing_later = db.get(LaterSeed, (tenant, listing_id))
    is_auto_later = db.scalar(select(Listing.listing_key).where(
        Listing.tenant_id == tenant, Listing.listing_key == listing_id,
        auto_later(tenant),
    ).limit(1)) is not None
    outcome = "created"
    if action == "pick":
        if existing_later is not None or is_auto_later:
            raise HTTPException(409, "Listing is already classified Later")
        if existing_pick is None:
            picked = Picked(tenant_id=tenant, listing_key=listing_id, picked_at=utcnow())
            db.add(picked)
            create_extraction_on_pick(db, tenant, source, picked.picked_at)
            queue_tiktok_skill_on_pick(db, tenant, source, picked.picked_at, automatic=True)
        else:
            outcome = "already"
    elif action == "later":
        if existing_pick is not None:
            raise HTTPException(409, "Unpick the listing before marking Later")
        if is_auto_later or existing_later is not None:
            outcome = "already"
        else:
            current = db.scalar(select(func.count()).select_from(LaterSeed)
                .where(LaterSeed.tenant_id == tenant)) or 0
            if current >= later_seed_limit:
                raise HTTPException(422, "Later topic seed limit reached")
            db.add(LaterSeed(
                tenant_id=tenant, listing_key=listing_id, title=source.title,
                analysis_json=seed_analysis(source.title), created_at=utcnow(),
            ))
            db.flush()
            refresh_later_matches(db, tenant)
    else:
        key = keyword.casefold()
        present = db.get(WinKeyword, (tenant, key))
        if present is None:
            current = db.scalar(select(func.count()).select_from(WinKeyword)
                .where(WinKeyword.tenant_id == tenant)) or 0
            if current >= win_keyword_limit:
                raise HTTPException(422, "Win Keyword limit reached")
            db.add(WinKeyword(tenant_id=tenant, keyword_key=key,
                              keyword=keyword, created_at=utcnow()))
        else:
            outcome = "already"

    receipt = Receipt(
        tenant_id=tenant, agent_id=agent.id, request_id=data["requestId"],
        listing_key=listing_id, action=action, keyword=keyword,
        evidence_hash=data["evidenceHash"], payload_sha256=fingerprint,
        outcome=outcome, confirmed_at=utcnow(),
    )
    db.add(receipt)
    db.commit()
    return _receipt_view(receipt)
