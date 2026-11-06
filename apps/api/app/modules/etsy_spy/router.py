"""First-party CAM Etsy Spy read-only dashboard and authenticated Windows push API.

The source SQLite stays on Windows. CAM never contacts its loopback listener.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, asc, desc, func, or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from .model import (
    EtsySpyAgent as Agent, EtsySpySession as SessionRow,
    EtsySpyKeyword as Keyword, EtsySpyListing as Listing,
    EtsySpyTrademark as Trademark, EtsySpyLog as Log,
    utcnow,
)
from .service import (
    MAX_BATCH_BYTES, MAX_INVENTORY_BYTES, authenticate_agent, create_agent,
    moment, reconcile_listing_inventory, sync_batch, text,
)

router = APIRouter(prefix="/api/v1/etsy-spy", tags=["etsy-spy"])
READ = require_permission("etsy_spy.read")
MANAGE = require_permission("etsy_spy.manage")
MAX_CSV_ROWS = 20_000

def freshness(db: Session, tenant: str) -> dict[str, Any]:
    rows = db.scalars(select(Agent).where(Agent.tenant_id == tenant, Agent.active.is_(True))).all()
    recent = max((a.last_seen_at for a in rows if a.last_seen_at), default=None)
    synced = max((a.last_synced_at for a in rows if a.last_synced_at), default=None)
    now = utcnow()
    def age(value):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return (now - value).total_seconds()
    status = ("never_synced" if synced is None else
              "offline" if recent is None or age(recent) > 120 else
              "sync_delayed" if age(synced) > 300 else "online")
    return {"source_status": status, "last_synced_at": synced,
            "last_seen_at": recent, "agent_count": len(rows),
            "online_agents": sum(1 for a in rows if a.last_seen_at and age(a.last_seen_at) <= 120)}


def count(db: Session, model: Any, tenant: str, conditions: list[Any] | None = None) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(
        model.tenant_id == tenant, *(conditions or [])
    )) or 0)


def _paged(db: Session, query: Any, total_query: Any, page: int, size: int) -> tuple[list, int]:
    total = int(db.scalar(total_query) or 0)
    rows = db.execute(query.limit(size).offset((page - 1) * size)).all()
    return rows, total


def _response(items: list, total: int, page: int, page_size: int, fresh: dict) -> dict:
    return jsonable_encoder({
        "items": items, "page": page, "page_size": page_size, "total": total,
        "pages": (total + page_size - 1) // page_size,
        "last_synced_at": fresh["last_synced_at"],
        "source_status": fresh["source_status"],
    })


def _direction(value: str):
    return asc if value == "asc" else desc


def _tm_label(check: Trademark | None) -> str:
    if check is None:
        return "pending"
    if check.status not in {"complete", "cached", "checked"}:
        return "pending" if check.status in {"pending", "running"} else "unverified"
    if check.live_count > 0:
        return "LIVE"
    if check.dead_count > 0:
        return "DEAD"
    return "no_match"


def _listings_query(
    tenant: str, *, q: str = "", session_id: str | None = None,
    source: str = "", badge: str = "", tm: str = "",
    sort: str = "observedAt", direction: str = "desc",
) -> tuple[Any, Any]:
    condition = [
        Listing.tenant_id == tenant,
        Listing.is_current.is_(True),
        # Defend against the source accidentally reporting an excluded Custom title.
        ~Listing.title.ilike("%custom%"),
    ]
    if q:
        condition.append(or_(Listing.title.ilike("%" + q[:160] + "%"),
                             Listing.keyword.ilike("%" + q[:160] + "%")))
    if session_id and session_id != "all":
        condition.append(Listing.source_session_id == session_id[:150])
    if source == "search":
        condition.append(Listing.has_search.is_(True))
    elif source == "shop":
        condition.append(Listing.has_shop.is_(True))
    if badge and badge != "all":
        # Match the source's respective badge, not a badge from another view.
        if source == "search":
            condition.append(Listing.search_badge.ilike(badge[:65]))
        elif source == "shop":
            condition.append(Listing.badge.ilike(badge[:65]))
        else:
            condition.append(or_(Listing.badge.ilike(badge[:65]),
                                 Listing.search_badge.ilike(badge[:65])))
    joined = and_(Trademark.tenant_id == Listing.tenant_id,
                  Trademark.agent_id == Listing.agent_id,
                  Trademark.check_id == Listing.tm_key,
                  Trademark.kind == "product")
    status = _tm_expression()
    if tm in {"LIVE", "DEAD", "no_match", "pending", "unverified"}:
        condition.append(status == tm)
    cols = {
        "observedAt": Listing.observed_at, "keyword": Listing.keyword,
        "sourceKind": Listing.source_kind, "badge": Listing.badge,
        "bought24h": Listing.bought24h, "views24h": Listing.views24h,
        "inCarts": Listing.in_carts, "demandScore": Listing.demand_score,
        "pageTitle": Listing.title, "tm": status,
    }
    column = cols.get(sort, Listing.observed_at)
    query = (select(Listing, Trademark)
             .outerjoin(Trademark, joined)
             .where(*condition)
             .order_by(_direction(direction)(column), Listing.source_session_id,
                       Listing.keyword, Listing.listing_key))
    total = (select(func.count()).select_from(Listing)
             .outerjoin(Trademark, joined).where(*condition))
    return query, total


def _tm_expression():
    from sqlalchemy import case
    return case(
        (Trademark.check_id.is_(None), "pending"),
        (Trademark.status.in_(["pending", "running"]), "pending"),
        (Trademark.status.notin_(["complete", "cached", "checked"]), "unverified"),
        (Trademark.live_count > 0, "LIVE"),
        (Trademark.dead_count > 0, "DEAD"),
        else_="no_match",
    )


def _listing_item(row: Listing, check: Trademark | None) -> dict:
    return {
        "sessionId": row.source_session_id, "keyword": row.keyword,
        "sourceKind": row.source_kind,
        "sourceKinds": [kind for kind, present in (
            ("search", row.has_search), ("shop", row.has_shop)) if present],
        "listingKey": row.listing_key,
        "pageTitle": row.title, "url": row.url, "badge": row.badge,
        "searchBadge": row.search_badge, "bought24h": row.bought24h,
        "views24h": row.views24h, "inCarts": row.in_carts,
        "demandScore": row.demand_score, "shopPosition": row.shop_position,
        "searchPosition": row.search_position, "sourceShopUrl": row.source_shop_url,
        "bestseller": row.bestseller, "observedAt": row.observed_at,
        "metadata": row.metadata_json,
        "tm": _tm_label(check), "tmStatus": check.status if check else "pending",
        "tmSource": check.source if check else None,
        "tmCheckedAt": check.checked_at if check else None,
        "tmSourceAsOf": check.source_as_of if check else None,
        "tmMatches": check.matched_json if check and _tm_label(check) in {"LIVE", "DEAD"} else [],
    }


@router.get("/summary")
def summary(
    principal: CurrentPrincipal = Depends(READ), db: Session = Depends(get_db),
):
    tenant = principal.active_tenant_id
    fresh = freshness(db, tenant)
    active = [Listing.tenant_id == tenant, Listing.is_current.is_(True),
              ~Listing.title.ilike("%custom%")]
    # Same denominator as the Windows Diagnostics: unique Etsy Listing IDs.
    # A listing may be discovered in *both* Search and Shop.
    unique = func.count(func.distinct(Listing.listing_key))
    listings_count = int(db.scalar(select(unique).where(*active)) or 0)
    search_count = int(db.scalar(select(unique).where(
        *active, Listing.has_search.is_(True))) or 0)
    shop_count = int(db.scalar(select(unique).where(
        *active, Listing.has_shop.is_(True))) or 0)
    tm_join = and_(
        Trademark.tenant_id == Listing.tenant_id,
        Trademark.agent_id == Listing.agent_id,
        Trademark.check_id == Listing.tm_key,
        Trademark.kind == "product",
    )
    tm_query = select(unique).select_from(Listing).outerjoin(
        Trademark, tm_join).where(*active)
    pending = int(db.scalar(tm_query.where(or_(
        Trademark.check_id.is_(None),
        Trademark.status.in_(["pending", "running"]),
    ))) or 0)
    checked = int(db.scalar(tm_query.where(
        Trademark.status.in_(["complete", "cached", "checked"]))) or 0)
    errors = count(db, Log, tenant, [Log.level == "error"])
    warnings = count(db, Log, tenant, [Log.level.in_(["warn", "warning"])])
    inventories = db.scalars(select(Agent).where(
        Agent.tenant_id == tenant, Agent.active.is_(True))).all()
    last_inventory = max(
        (a.last_inventory_at for a in inventories if a.last_inventory_at),
        default=None)
    return jsonable_encoder({
        **fresh, "sessions": count(db, SessionRow, tenant),
        "keywords": count(db, Keyword, tenant),
        "listings": listings_count, "search": search_count, "shop": shop_count,
        "trademarks": count(db, Trademark, tenant),
        "tm_pending": pending, "tm_checked": checked,
        "last_inventory_at": last_inventory,
        "errors": errors, "warnings": warnings,
    })


@router.get("/sessions")
def sessions(principal: CurrentPrincipal = Depends(READ), db: Session = Depends(get_db)):
    tenant = principal.active_tenant_id
    rows = db.scalars(select(SessionRow).where(SessionRow.tenant_id == tenant)
                      .order_by(desc(SessionRow.updated_at), SessionRow.source_session_id).limit(500)).all()
    return jsonable_encoder({"items": [
        {"id": row.source_session_id, "status": row.status, "phase": row.phase,
         "keyword": row.keyword, "mode": row.mode, "revision": row.revision,
         "startedAt": row.started_at, "updatedAt": row.updated_at}
        for row in rows
    ], **freshness(db, tenant)})


@router.get("/listings")
def listings(
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    q: str = "", session_id: str | None = None, source: str = "",
    badge: str = "", tm: str = "", sort: str = "observedAt", dir: str = "desc",
    principal: CurrentPrincipal = Depends(READ), db: Session = Depends(get_db),
):
    tenant = principal.active_tenant_id
    query, total_q = _listings_query(
        tenant, q=q, session_id=session_id, source=source, badge=badge,
        tm=tm, sort=sort, direction=dir,
    )
    rows, total = _paged(db, query, total_q, page, page_size)
    return _response([_listing_item(listing, tmcheck) for listing, tmcheck in rows],
                     total, page, page_size, freshness(db, tenant))


@router.get("/keywords")
def keywords(
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    q: str = "", session_id: str | None = None, status: str = "",
    sort: str = "updatedAt", dir: str = "desc",
    principal: CurrentPrincipal = Depends(READ), db: Session = Depends(get_db),
):
    tenant = principal.active_tenant_id
    where = [Keyword.tenant_id == tenant]
    if q: where.append(Keyword.keyword.ilike("%" + q[:160] + "%"))
    if session_id and session_id != "all": where.append(Keyword.source_session_id == session_id[:150])
    if status and status != "all": where.append(Keyword.status == status[:50])
    columns = {
        "updatedAt": Keyword.updated_at, "keyword": Keyword.keyword,
        "depth": Keyword.depth, "priority": Keyword.priority,
        "status": Keyword.status, "matchedCount": Keyword.matched_count,
    }
    rows, total = _paged(db,
        select(Keyword).where(*where).order_by(
            _direction(dir)(columns.get(sort, Keyword.updated_at)), Keyword.node_id),
        select(func.count()).select_from(Keyword).where(*where), page, page_size,
    )
    return _response([{
        "sessionId": row.source_session_id, "id": row.node_id,
        "parentId": row.parent_id, "keyword": row.keyword, "depth": row.depth,
        "priority": row.priority, "status": row.status, "reason": row.reason,
        "evidence": row.evidence_json, "matchedCount": row.matched_count,
        "sourceUrl": row.source_url, "updatedAt": row.updated_at,
    } for (row,) in rows], total, page, page_size, freshness(db, tenant))


@router.get("/trademarks")
def trademarks(
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    q: str = "", status: str = "", kind: str = "",
    sort: str = "checkedAt", dir: str = "desc",
    principal: CurrentPrincipal = Depends(READ), db: Session = Depends(get_db),
):
    tenant = principal.active_tenant_id
    where = [Trademark.tenant_id == tenant]
    if q: where.append(or_(Trademark.keyword.ilike("%" + q[:160] + "%"),
                           Trademark.title.ilike("%" + q[:160] + "%")))
    if status: where.append(Trademark.status == status[:35])
    if kind: where.append(Trademark.kind == kind[:30])
    cols = {"checkedAt": Trademark.checked_at, "keyword": Trademark.keyword,
            "title": Trademark.title, "status": Trademark.status,
            "liveCount": Trademark.live_count}
    rows, total = _paged(db,
        select(Trademark).where(*where).order_by(
            _direction(dir)(cols.get(sort, Trademark.checked_at)), Trademark.check_id),
        select(func.count()).select_from(Trademark).where(*where), page, page_size,
    )
    return _response([{
        "checkId": row.check_id, "kind": row.kind,
        "keyword": row.keyword, "title": row.title, "status": row.status,
        "risk": row.risk, "liveCount": row.live_count,
        "deadCount": row.dead_count, "unknownCount": row.unknown_count,
        "matched": row.matched_json, "checkedAt": row.checked_at,
        "source": row.source, "sourceAsOf": row.source_as_of,
    } for (row,) in rows], total, page, page_size, freshness(db, tenant))


@router.post("/trademarks/lookup")
async def trademark_lookup(
    request: Request, principal: CurrentPrincipal = Depends(READ),
    db: Session = Depends(get_db),
):
    # Read-only, cached EXACT/PARTIAL lookup. No remote TMHunt call or queue.
    data = await request.json()
    queries = data.get("queries") if isinstance(data, dict) else None
    mode = data.get("mode") if isinstance(data, dict) else None
    if (not isinstance(queries, list) or not 1 <= len(queries) <= 30
            or any(not isinstance(x, str) or not 1 <= len(x.strip()) <= 160 for x in queries)
            or mode not in {"exactMatch", "partialMatch"}):
        raise HTTPException(422, "Provide 1–30 queries and exactMatch or partialMatch")
    normalized = [" ".join(re.findall(r"[a-z0-9]+", x.casefold())) for x in queries]
    if any(not term for term in normalized):
        raise HTTPException(422, "Empty normalized trademark query")
    cached = db.scalars(select(Trademark).where(
        Trademark.tenant_id == principal.active_tenant_id,
        Trademark.matched_json.is_not(None),
    ).limit(10_000)).all()
    seen = {}
    for item in cached:
        for match in item.matched_json or []:
            name = " ".join(re.findall(r"[a-z0-9]+", str(match.get("markText") or "").casefold()))
            if not name: continue
            if any(name == query if mode == "exactMatch" else query in name for query in normalized):
                key = match.get("serialNumber") or (name + ":" + str(match.get("liveStatus") or ""))
                seen[key] = match
    return {"queries": queries, "mode": mode, "matched": list(seen.values())[:200],
            "total": len(seen), "source": "CAM cached Windows TM evidence",
            "advisory": "No match is not legal clearance. Verify current records with USPTO."}


def _log_query(
    tenant: str, *, q: str = "", level: str = "", source: str = "",
    phase: str = "", session_id: str | None = None, keyword: str = "",
    sort: str = "at", direction: str = "desc",
):
    where = [Log.tenant_id == tenant]
    if q: where.append(or_(Log.message.ilike("%" + q[:160] + "%"),
                           Log.stack.ilike("%" + q[:160] + "%")))
    if level: where.append(Log.level == level[:25])
    if source: where.append(Log.source == source[:80])
    if phase: where.append(Log.phase == phase[:100])
    if keyword: where.append(Log.keyword.ilike("%" + keyword[:160] + "%"))
    if session_id and session_id != "all": where.append(Log.source_session_id == session_id[:150])
    cols = {"at": Log.event_at, "level": Log.level, "source": Log.source,
            "phase": Log.phase, "keyword": Log.keyword}
    return (select(Log).where(*where).order_by(
        _direction(direction)(cols.get(sort, Log.event_at)), Log.event_id),
        select(func.count()).select_from(Log).where(*where), where)


def _log_item(row: Log) -> dict:
    return {"eventId": row.event_id, "sessionId": row.source_session_id,
            "at": row.event_at, "level": row.level, "source": row.source,
            "category": row.category, "action": row.action, "phase": row.phase,
            "keyword": row.keyword, "message": row.message, "stack": row.stack}


@router.get("/logs")
def logs(
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    q: str = "", level: str = "", source: str = "",
    phase: str = "", session_id: str | None = None, keyword: str = "",
    sort: str = "at", dir: str = "desc",
    principal: CurrentPrincipal = Depends(READ), db: Session = Depends(get_db),
):
    tenant = principal.active_tenant_id
    query, totals, _ = _log_query(
        tenant, q=q, level=level, source=source, phase=phase,
        session_id=session_id, keyword=keyword, sort=sort, direction=dir,
    )
    rows, total = _paged(db, query, totals, page, page_size)
    return _response([_log_item(row) for (row,) in rows], total, page, page_size,
                     freshness(db, tenant))


@router.get("/logs/stats")
def log_stats(
    level: str = "", source: str = "", phase: str = "",
    session_id: str | None = None, keyword: str = "", q: str = "",
    principal: CurrentPrincipal = Depends(READ), db: Session = Depends(get_db),
):
    _, _, where = _log_query(principal.active_tenant_id, q=q, level=level,
                             source=source, phase=phase,
                             session_id=session_id, keyword=keyword)
    records = db.execute(select(Log.level, func.count()).where(*where).group_by(Log.level)).all()
    levels = {name or "unknown": int(value) for name, value in records}
    return {"total": sum(levels.values()), "errors": levels.get("error", 0),
            "warnings": levels.get("warning", 0) + levels.get("warn", 0),
            "levels": levels}


def _csv_cell(value: Any) -> str:
    if value is None: return ""
    if isinstance(value, datetime): value = value.isoformat()
    value = str(value)
    if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return "'" + value
    return value[:4000]


def _csv_response(headers: list[str], rows: list[dict], filename: str):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_csv_cell(row.get(key)) for key in headers])
    body = "\ufeff" + buffer.getvalue()
    return StreamingResponse(iter((body,)), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"',
                 "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                 "X-Export-Limit": str(MAX_CSV_ROWS)})


@router.get("/export.csv")
def export_listings(
    q: str = "", session_id: str | None = None, source: str = "",
    badge: str = "", tm: str = "", sort: str = "observedAt", dir: str = "desc",
    principal: CurrentPrincipal = Depends(READ), db: Session = Depends(get_db),
):
    query, _ = _listings_query(principal.active_tenant_id, q=q, session_id=session_id,
                              source=source, badge=badge, tm=tm, sort=sort, direction=dir)
    rows = db.execute(query.limit(MAX_CSV_ROWS)).all()
    headers = ["sessionId", "keyword", "sourceKind", "badge", "tm", "bought24h",
               "views24h", "inCarts", "demandScore", "pageTitle", "url", "observedAt"]
    return _csv_response(headers, [_listing_item(listing, check) for listing, check in rows],
                         "CAM-Etsy-Spy-Listings.csv")


@router.get("/logs/export.csv")
def export_logs(
    q: str = "", level: str = "", source: str = "", phase: str = "",
    session_id: str | None = None, keyword: str = "",
    sort: str = "at", dir: str = "desc",
    principal: CurrentPrincipal = Depends(READ), db: Session = Depends(get_db),
):
    query, _, _ = _log_query(principal.active_tenant_id, q=q, level=level,
        source=source, phase=phase, session_id=session_id, keyword=keyword,
        sort=sort, direction=dir)
    headers = ["at", "level", "source", "category", "action", "phase",
               "sessionId", "keyword", "message", "stack", "eventId"]
    return _csv_response(headers, [_log_item(row) for (row,) in db.execute(
        query.limit(MAX_CSV_ROWS)).all()], "CAM-Etsy-Spy-Logs.csv")


@router.get("/agents")
def list_agents(
    principal: CurrentPrincipal = Depends(READ), db: Session = Depends(get_db),
):
    agents = db.scalars(select(Agent).where(Agent.tenant_id == principal.active_tenant_id)
                        .order_by(desc(Agent.created_at))).all()
    return jsonable_encoder({"items": [
        {"id": item.id, "name": item.name, "machine_label": item.machine_label,
         "active": item.active, "extension_version": item.extension_version,
         "local_api_version": item.local_api_version, "last_seen_at": item.last_seen_at,
         "last_synced_at": item.last_synced_at, "cursor": item.last_cursor}
        for item in agents
    ]})


@router.post("/agents", status_code=201)
async def pair_agent(
    request: Request, principal: CurrentPrincipal = Depends(MANAGE),
    db: Session = Depends(get_db),
):
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(422, "Invalid request")
    name = text(payload.get("name"), 100, required=True)
    agent, secret = create_agent(db, principal.active_tenant_id, name)
    return {"agent_id": agent.id, "token": secret,
            "notice": "Save this token on the Windows tool now. It will not be shown again."}


@router.post("/agents/{agent_id}/revoke")
def revoke_agent(
    agent_id: str, principal: CurrentPrincipal = Depends(MANAGE),
    db: Session = Depends(get_db),
):
    row = db.get(Agent, agent_id)
    if row is None or row.tenant_id != principal.active_tenant_id:
        raise HTTPException(404, "Agent not found")
    row.active = False
    db.commit()
    return {"ok": True}


@router.post("/agents/{agent_id}/rotate")
def rotate_agent(
    agent_id: str, principal: CurrentPrincipal = Depends(MANAGE),
    db: Session = Depends(get_db),
):
    import hashlib, secrets
    row = db.get(Agent, agent_id)
    if row is None or row.tenant_id != principal.active_tenant_id or not row.active:
        raise HTTPException(404, "Agent not found")
    secret = secrets.token_urlsafe(48)
    row.token_hash = hashlib.sha256(secret.encode()).hexdigest()
    db.commit()
    return {"agent_id": row.id, "token": secret,
            "notice": "The previous credential has been revoked. Save this token once."}


@router.post("/agents/{agent_id}/heartbeat")
async def agent_heartbeat(
    agent_id: str, request: Request, authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    agent = authenticate_agent(db, agent_id, authorization)
    if int(request.headers.get("content-length", "0") or "0") > 4096:
        raise HTTPException(413, "Heartbeat too large")
    data = await request.json()
    if not isinstance(data, dict): raise HTTPException(422, "Invalid heartbeat")
    agent.machine_label = text(data.get("machine_label"), 100)
    agent.extension_version = text(data.get("extension_version"), 40)
    agent.local_api_version = text(data.get("local_api_version"), 40)
    agent.last_seen_at = utcnow()
    db.commit()
    return {"ok": True, "agent_id": agent.id, "server_time": utcnow()}


@router.get("/agents/{agent_id}/sync-status")
def agent_sync_status(
    agent_id: str, authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    agent = authenticate_agent(db, agent_id, authorization)
    return jsonable_encoder({"ok": True, "cursor": agent.last_cursor,
                             "last_synced_at": agent.last_synced_at,
                             "last_inventory_at": agent.last_inventory_at,
                             "last_inventory_count": agent.last_inventory_count,
                             "active": agent.active})


@router.post("/agents/{agent_id}/listing-inventory")
async def ingest_listing_inventory(
    agent_id: str, request: Request, authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """Reconcile the complete current Windows listing projection, not its history."""
    agent = authenticate_agent(db, agent_id, authorization)
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_INVENTORY_BYTES:
            raise HTTPException(413, "Listing inventory too large")
        chunks.append(chunk)
    return reconcile_listing_inventory(db, agent, b"".join(chunks))


@router.post("/agents/{agent_id}/sync")
async def ingest_agent(
    agent_id: str, request: Request, authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    agent = authenticate_agent(db, agent_id, authorization)
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BATCH_BYTES: raise HTTPException(413, "Sync batch too large")
        chunks.append(chunk)
    return sync_batch(db, agent, b"".join(chunks))
