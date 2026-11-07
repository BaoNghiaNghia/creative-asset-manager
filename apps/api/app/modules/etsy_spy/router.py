"""First-party CAM Etsy Spy read-only dashboard and authenticated Windows push API.

The source SQLite stays on Windows. CAM never contacts its loopback listener.
"""
from __future__ import annotations

import csv
import io
import re
import hashlib
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, asc, desc, func, or_, select, not_, literal
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from .model import (
    EtsySpyAgent as Agent, EtsySpyBlacklist as Blacklist, EtsySpyPicked as Picked,
    EtsySpyWinKeyword as WinKeyword,
    EtsySpyParentKeyword as ParentKeyword,
    EtsySpySession as SessionRow,
    EtsySpyKeyword as Keyword, EtsySpyListing as Listing,
    EtsySpyTrademark as Trademark, EtsySpyLog as Log,
    utcnow,
)
from .service import (
    MAX_BATCH_BYTES, MAX_INVENTORY_BYTES, authenticate_agent, create_agent,
    moment, reconcile_listing_inventory, sync_batch, text, system_log_cutoff,
    verified_image_url,
)

router = APIRouter(prefix="/api/v1/etsy-spy", tags=["etsy-spy"])
READ = require_permission("etsy_spy.read")
MANAGE = require_permission("etsy_spy.manage")
MAX_CSV_ROWS = 20_000
VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
OBSERVED_DAY_START = time(6, 0)

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


def _normalized_phrase(value: str) -> str:
    """Case-insensitive whole-word/phrase match, ignoring ASCII punctuation."""
    return " ".join(re.findall(r"[a-z0-9]+", str(value).casefold()))[:120]


def _blacklist_condition(tenant: str):
    # Portable SQL for PostgreSQL and SQLite test fixtures. Preserve phrase
    # boundaries: 'cat' must not match 'caterpillar'. The source row is never
    # deleted; removing a phrase restores its visibility.
    title = func.lower(Listing.title)
    for char in ('-', '_', '.', '/', '&', ','):
        title = func.replace(title, char, " ")
    for _ in range(2):
        title = func.replace(title, "  ", " ")
    padded = literal(" ") + func.trim(title) + literal(" ")
    forbidden = (select(literal(1)).select_from(Blacklist)
        .where(Blacklist.tenant_id == tenant,
               padded.like(literal("% ") + Blacklist.phrase + literal(" %")))
        .exists())
    return not_(forbidden)


def _blacklist_view(db: Session, tenant: str) -> dict:
    phrases = list(db.scalars(select(Blacklist.phrase).where(
        Blacklist.tenant_id == tenant).order_by(Blacklist.phrase)).all())
    fingerprint = hashlib.sha256("\n".join(phrases).encode("utf-8")).hexdigest()[:16]
    return {"phrases": phrases, "revision": fingerprint, "count": len(phrases)}


def _valid_source_badge(source: str):
    """Do not borrow a badge from another Search/Shop observation."""
    allowed = ("bestseller", "popular now")
    if source == "search":
        return and_(Listing.has_search.is_(True),
                    func.lower(func.trim(func.coalesce(Listing.search_badge, ""))).in_(allowed))
    return and_(Listing.has_shop.is_(True),
                func.lower(func.trim(func.coalesce(Listing.badge, ""))).in_(allowed))


def _qualified_listing_condition():
    """Source badge AND (views in 24h > 5 OR 20+ carts) are required."""
    return and_(or_(Listing.views24h > 5, Listing.in_carts >= 20),
                or_(_valid_source_badge("search"), _valid_source_badge("shop")))


def _picked_condition(tenant: str):
    # One selection per tenant and Etsy Listing ID, regardless of the
    # number of sessions, keywords or Search/Shop observations.
    return (select(literal(1)).select_from(Picked)
            .where(Picked.tenant_id == tenant,
                   Picked.listing_key == Listing.listing_key)
            .exists())


def _blacklist_visible_count(db: Session, tenant: str):
    return int(db.scalar(select(func.count(func.distinct(Listing.listing_key))).where(
        Listing.tenant_id == tenant, Listing.is_current.is_(True),
        ~Listing.title.ilike("%custom%"), _blacklist_condition(tenant),
        _qualified_listing_condition(), not_(_picked_condition(tenant)))) or 0)


@router.get("/blacklist")
def list_blacklist(principal: CurrentPrincipal = Depends(READ),
                   db: Session = Depends(get_db)):
    return _blacklist_view(db, principal.active_tenant_id)


@router.post("/blacklist")
async def add_blacklist(request: Request, principal: CurrentPrincipal = Depends(MANAGE),
                        db: Session = Depends(get_db)):
    body = await request.json()
    raw = body.get("phrases") if isinstance(body, dict) else None
    if not isinstance(raw, list) or not 1 <= len(raw) <= 40:
        raise HTTPException(422, "Provide 1-40 blacklist phrases")
    values = []
    for item in raw:
        if not isinstance(item, str) or len(item) > 150:
            raise HTTPException(422, "Invalid blacklist phrase")
        phrase = _normalized_phrase(item)
        if len(phrase) < 2:
            raise HTTPException(422, "Blacklist phrase must contain at least 2 characters")
        values.append(phrase)
    tenant = principal.active_tenant_id
    current = set(db.scalars(select(Blacklist.phrase).where(
        Blacklist.tenant_id == tenant)).all())
    if len(current | set(values)) > 500:
        raise HTTPException(422, "Blacklist limited to 500 phrases")
    for phrase in set(values) - current:
        db.add(Blacklist(tenant_id=tenant, phrase=phrase, created_at=utcnow()))
    db.commit()
    return {**_blacklist_view(db, tenant),
            "visible_products": _blacklist_visible_count(db, tenant)}


@router.delete("/blacklist/{phrase}")
def remove_blacklist(phrase: str, principal: CurrentPrincipal = Depends(MANAGE),
                     db: Session = Depends(get_db)):
    normalized = _normalized_phrase(phrase)
    if normalized:
        item = db.get(Blacklist, (principal.active_tenant_id, normalized))
        if item is not None:
            db.delete(item)
            db.commit()
    return {**_blacklist_view(db, principal.active_tenant_id),
            "visible_products": _blacklist_visible_count(db, principal.active_tenant_id)}


def _win_keyword_value(raw: Any) -> str:
    """Keep human-entered wording and punctuation, de-duplicate case-insensitively."""
    if not isinstance(raw, str) or len(raw) > 220:
        raise HTTPException(422, "Win Keyword must be a string of at most 220 characters")
    keyword = " ".join(raw.split())
    if len(keyword) < 2 or len(keyword) > 220:
        raise HTTPException(422, "Win Keyword must contain 2-220 characters")
    return keyword


def _win_keyword_view(db: Session, tenant: str) -> dict:
    rows = db.scalars(
        select(WinKeyword).where(WinKeyword.tenant_id == tenant)
        .order_by(WinKeyword.keyword_key)
    ).all()
    return {"keywords": [row.keyword for row in rows], "count": len(rows)}


@router.get("/win-keywords")
def list_win_keywords(
    principal: CurrentPrincipal = Depends(READ), db: Session = Depends(get_db),
):
    return _win_keyword_view(db, principal.active_tenant_id)


@router.post("/win-keywords")
async def add_win_keywords(
    request: Request, principal: CurrentPrincipal = Depends(MANAGE),
    db: Session = Depends(get_db),
):
    payload = await request.json()
    raw = payload.get("keywords") if isinstance(payload, dict) else None
    if not isinstance(raw, list) or not 1 <= len(raw) <= 40:
        raise HTTPException(422, "Provide 1-40 Win Keywords")
    keywords = {}
    for item in raw:
        keyword = _win_keyword_value(item)
        keywords.setdefault(keyword.casefold(), keyword)
    tenant = principal.active_tenant_id
    current = set(db.scalars(select(WinKeyword.keyword_key)
                             .where(WinKeyword.tenant_id == tenant)).all())
    if len(current | set(keywords)) > 1000:
        raise HTTPException(422, "Win Keyword list is limited to 1000 entries")
    for key, keyword in keywords.items():
        if key not in current:
            db.add(WinKeyword(tenant_id=tenant, keyword_key=key,
                              keyword=keyword, created_at=utcnow()))
    db.commit()
    return _win_keyword_view(db, tenant)


@router.delete("/win-keywords")
async def remove_win_keyword(
    request: Request, principal: CurrentPrincipal = Depends(MANAGE),
    db: Session = Depends(get_db),
):
    payload = await request.json()
    keyword = _win_keyword_value(payload.get("keyword") if isinstance(payload, dict) else None)
    item = db.get(WinKeyword, (principal.active_tenant_id, keyword.casefold()))
    if item is not None:
        db.delete(item)
        db.commit()
    return _win_keyword_view(db, principal.active_tenant_id)


def _parent_keyword_name(raw: Any) -> str:
    if not isinstance(raw, str) or len(raw) > 180:
        raise HTTPException(422, "Parent keyword must be a string of 2-180 characters")
    name = " ".join(raw.split())
    if len(name) < 2 or len(name) > 180:
        raise HTTPException(422, "Parent keyword must be 2-180 characters")
    return name


def _parent_keyword_key(name: str) -> str:
    return name.casefold()


def _parent_keyword_items(db: Session, tenant: str) -> dict:
    # Historical root nodes remain read-only. Manually configured settings
    # override the display and scheduler priority without rewriting Scout rows.
    roots = db.execute(select(Keyword.keyword).where(
        Keyword.tenant_id == tenant, Keyword.parent_id.is_(None),
        Keyword.depth == 0).order_by(desc(Keyword.updated_at)).limit(20_000)).scalars()
    items: dict[str, dict] = {}
    for raw in roots:
        name = " ".join((raw or "").split())
        if len(name) < 2 or len(name) > 180:
            continue
        key = _parent_keyword_key(name)
        if key not in items:
            items[key] = {"keyword": name, "keywordKey": key, "priority": 50,
                          "source": "observed", "occurrences": 0}
        items[key]["occurrences"] += 1
    rows = db.scalars(select(ParentKeyword).where(
        ParentKeyword.tenant_id == tenant)).all()
    for row in rows:
        previous = items.get(row.keyword_key)
        items[row.keyword_key] = {
            "keyword": row.keyword, "keywordKey": row.keyword_key,
            "priority": row.priority, "source": row.source,
            "occurrences": previous["occurrences"] if previous else 0,
        }
    ordered = sorted(items.values(), key=lambda item: (
        -item["priority"], item["keyword"].casefold()))
    fingerprint = hashlib.sha256("|".join(
        f'{item["keywordKey"]}:{item["priority"]}:{item["source"]}'
        for item in ordered).encode("utf-8")).hexdigest()[:16]
    return {"items": ordered, "count": len(ordered), "revision": fingerprint}


@router.get("/parent-keywords")
def list_parent_keywords(
    principal: CurrentPrincipal = Depends(READ), db: Session = Depends(get_db),
):
    return _parent_keyword_items(db, principal.active_tenant_id)


async def _upsert_parent_keyword(request: Request, tenant: str, db: Session) -> dict:
    try:
        body = await request.json()
    except (ValueError, TypeError):
        raise HTTPException(422, "JSON object required") from None
    if not isinstance(body, dict):
        raise HTTPException(422, "JSON object required")
    name = _parent_keyword_name(body.get("keyword"))
    priority = body.get("priority", 50)
    if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 100:
        raise HTTPException(422, "Priority must be an integer from 1 to 100")
    key = _parent_keyword_key(name)
    row = db.get(ParentKeyword, (tenant, key))
    if row is None:
        current = _parent_keyword_items(db, tenant)
        if key not in {entry["keywordKey"] for entry in current["items"]} and current["count"] >= 200:
            raise HTTPException(422, "Parent keyword list limited to 200 entries")
        origin = "observed" if any(entry["keywordKey"] == key for entry in current["items"]) else "manual"
        db.add(ParentKeyword(tenant_id=tenant, keyword_key=key, keyword=name,
                             priority=priority, source=origin,
                             created_at=utcnow(), updated_at=utcnow()))
    else:
        row.priority = priority
        row.updated_at = utcnow()
    db.commit()
    return _parent_keyword_items(db, tenant)


@router.post("/parent-keywords")
async def add_parent_keyword(
    request: Request, principal: CurrentPrincipal = Depends(MANAGE),
    db: Session = Depends(get_db),
):
    return await _upsert_parent_keyword(request, principal.active_tenant_id, db)


@router.put("/parent-keywords")
async def update_parent_priority(
    request: Request, principal: CurrentPrincipal = Depends(MANAGE),
    db: Session = Depends(get_db),
):
    return await _upsert_parent_keyword(request, principal.active_tenant_id, db)


def _observed_day_bounds(day: date) -> tuple[datetime, datetime]:
    """The Vietnamese calendar day begins at 06:00, with an exclusive end."""
    start = datetime.combine(day, OBSERVED_DAY_START, tzinfo=VIETNAM_TZ)
    end = datetime.combine(day + timedelta(days=1), OBSERVED_DAY_START, tzinfo=VIETNAM_TZ)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _listings_query(
    tenant: str, *, q: str = "", session_id: str | None = None,
    source: str = "", badge: str = "", tm: str = "",
    sort: str = "observedAt", direction: str = "desc",
    pick_state: str = "eligible", observed_day: date | None = None,
) -> tuple[Any, Any]:
    if pick_state not in {"eligible", "picked"}:
        raise HTTPException(422, "Invalid Picked view")
    condition = [
        Listing.tenant_id == tenant,
        Listing.is_current.is_(True),
        # Defend against the source accidentally reporting an excluded Custom title.
        ~Listing.title.ilike("%custom%"),
        _blacklist_condition(tenant),
        _qualified_listing_condition(),
        _picked_condition(tenant) if pick_state == "picked"
        else not_(_picked_condition(tenant)),
    ]
    if observed_day is not None:
        since, before = _observed_day_bounds(observed_day)
        condition.extend((Listing.observed_at >= since, Listing.observed_at < before))
    if q:
        condition.append(or_(Listing.title.ilike("%" + q[:160] + "%"),
                             Listing.keyword.ilike("%" + q[:160] + "%")))
    if session_id and session_id != "all":
        condition.append(Listing.source_session_id == session_id[:150])
    if source in {"search", "shop"}:
        condition.append(_valid_source_badge(source))
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


def _listing_item(row: Listing, check: Trademark | None, *, picked: bool = False) -> dict:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    return {
        "sessionId": row.source_session_id, "keyword": row.keyword,
        "sourceKind": row.source_kind,
        "sourceKinds": [kind for kind, present in (
            ("search", row.has_search), ("shop", row.has_shop)) if present],
        "listingKey": row.listing_key, "picked": picked,
        "pageTitle": row.title, "url": row.url, "badge": row.badge,
        "thumbnailUrl": verified_image_url(metadata.get("thumbnailUrl")),
        "fullImageUrl": verified_image_url(metadata.get("fullImageUrl")),
        "searchBadge": row.search_badge, "bought24h": row.bought24h,
        "views24h": row.views24h, "inCarts": row.in_carts,
        "demandScore": row.demand_score, "shopPosition": row.shop_position,
        "searchPosition": row.search_position, "sourceShopUrl": row.source_shop_url,
        "bestseller": row.bestseller, "observedAt": row.observed_at,
        "metadata": row.metadata_json,
        "keywordSms": metadata.get("keywordSms", []) if isinstance(metadata.get("keywordSms"), list) else [],
        "keywordSmsQuery": metadata.get("keywordSmsQuery", ""),
        "keywordSmsStatus": metadata.get("keywordSmsStatus", "not_checked"),
        "keywordSmsCheckedAt": metadata.get("keywordSmsCheckedAt"),
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
              ~Listing.title.ilike("%custom%"), _blacklist_condition(tenant),
              _qualified_listing_condition()]
    # Same denominator as the Windows Diagnostics: unique Etsy Listing IDs.
    # A listing may be discovered in *both* Search and Shop.
    unique = func.count(func.distinct(Listing.listing_key))
    picked_filter = _picked_condition(tenant)
    eligible = [*active, not_(picked_filter)]
    listings_count = int(db.scalar(select(unique).where(*eligible)) or 0)
    picked_count = int(db.scalar(select(unique).where(*active, picked_filter)) or 0)
    search_count = int(db.scalar(select(unique).where(
        *eligible, _valid_source_badge("search"))) or 0)
    shop_count = int(db.scalar(select(unique).where(
        *eligible, _valid_source_badge("shop"))) or 0)
    tm_join = and_(
        Trademark.tenant_id == Listing.tenant_id,
        Trademark.agent_id == Listing.agent_id,
        Trademark.check_id == Listing.tm_key,
        Trademark.kind == "product",
    )
    tm_query = select(unique).select_from(Listing).outerjoin(
        Trademark, tm_join).where(*eligible)
    pending = int(db.scalar(tm_query.where(or_(
        Trademark.check_id.is_(None),
        Trademark.status.in_(["pending", "running"]),
    ))) or 0)
    checked = int(db.scalar(tm_query.where(
        Trademark.status.in_(["complete", "cached", "checked"]))) or 0)
    errors = count(db, Log, tenant, [Log.level == "error", Log.event_at >= system_log_cutoff()])
    warnings = count(db, Log, tenant, [Log.level.in_(["warn", "warning"]), Log.event_at >= system_log_cutoff()])
    inventories = db.scalars(select(Agent).where(
        Agent.tenant_id == tenant, Agent.active.is_(True))).all()
    last_inventory = max(
        (a.last_inventory_at for a in inventories if a.last_inventory_at),
        default=None)
    return jsonable_encoder({
        **fresh, "sessions": count(db, SessionRow, tenant),
        "keywords": count(db, Keyword, tenant),
        "listings": listings_count, "picked": picked_count,
        "search": search_count, "shop": shop_count,
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


@router.get("/listings/observed-days")
def listing_observed_days(
    principal: CurrentPrincipal = Depends(READ), db: Session = Depends(get_db),
):
    """Ten recent 06:00–06:00 VN buckets of current eligible Etsy Listing IDs.

    Reuse the same qualification, blacklist and Pick rules as /listings.
    A single SQL aggregate avoids one round trip per date and duplicate IDs.
    """
    today = datetime.now(VIETNAM_TZ).date()
    days = [today - timedelta(days=index) for index in range(10)]
    windows = [_observed_day_bounds(day) for day in days]
    expressions = [
        func.count(func.distinct(Listing.listing_key)).filter(
            Listing.observed_at >= since, Listing.observed_at < before,
        )
        for since, before in windows
    ]
    _, total_query = _listings_query(principal.active_tenant_id)
    counts = db.execute(total_query.with_only_columns(
        *expressions, maintain_column_froms=True,
    ).where(
        Listing.observed_at >= windows[-1][0],
        Listing.observed_at < windows[0][1],
    )).one()
    return {
        "timezone": "Asia/Ho_Chi_Minh",
        "start_hour": 6,
        "items": [
            {"day": day.isoformat(), "count": int(counts[index] or 0)}
            for index, day in enumerate(days)
        ],
    }


@router.get("/listings")
def listings(
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    q: str = "", session_id: str | None = None, source: str = "",
    badge: str = "", tm: str = "", sort: str = "observedAt", dir: str = "desc",
    pick_state: str = "eligible", observed_day: date | None = None,
    principal: CurrentPrincipal = Depends(READ), db: Session = Depends(get_db),
):
    tenant = principal.active_tenant_id
    query, total_q = _listings_query(
        tenant, q=q, session_id=session_id, source=source, badge=badge,
        tm=tm, sort=sort, direction=dir, pick_state=pick_state,
        observed_day=observed_day,
    )
    rows, total = _paged(db, query, total_q, page, page_size)
    return _response([_listing_item(listing, tmcheck, picked=pick_state == "picked")
                      for listing, tmcheck in rows],
                     total, page, page_size, freshness(db, tenant))


@router.put("/listings/{listing_key}/pick")
async def set_listing_pick(
    listing_key: str, request: Request,
    principal: CurrentPrincipal = Depends(MANAGE), db: Session = Depends(get_db),
):
    """Idempotent, reversible user decision independent of Windows sync rows."""
    if not re.fullmatch(r"[0-9]{5,20}", listing_key):
        raise HTTPException(422, "Invalid Etsy Listing ID")
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(422, "JSON object required") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("picked"), bool):
        raise HTTPException(422, "picked must be true or false")
    tenant = principal.active_tenant_id
    target = db.get(Picked, (tenant, listing_key))
    if payload["picked"]:
        # Never pick an unqualified, blacklisted or foreign-tenant product.
        eligible = db.scalar(select(literal(1)).select_from(Listing).where(
            Listing.tenant_id == tenant,
            Listing.listing_key == listing_key,
            Listing.is_current.is_(True),
            ~Listing.title.ilike("%custom%"),
            _blacklist_condition(tenant),
            _qualified_listing_condition(),
        ).limit(1))
        if eligible is None:
            raise HTTPException(404, "Eligible listing not found")
        if target is None:
            db.add(Picked(tenant_id=tenant, listing_key=listing_key,
                          picked_at=utcnow()))
    elif target is not None:
        db.delete(target)
    db.commit()
    return {"listingKey": listing_key, "picked": payload["picked"]}


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
    where = [Log.tenant_id == tenant, Log.event_at >= system_log_cutoff()]
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
    pick_state: str = "eligible", observed_day: date | None = None,
    principal: CurrentPrincipal = Depends(READ), db: Session = Depends(get_db),
):
    query, _ = _listings_query(principal.active_tenant_id, q=q, session_id=session_id,
                              source=source, badge=badge, tm=tm,
                              sort=sort, direction=dir, pick_state=pick_state,
                              observed_day=observed_day)
    rows = db.execute(query.limit(MAX_CSV_ROWS)).all()
    headers = ["sessionId", "keyword", "sourceKind", "badge", "tm", "bought24h",
               "views24h", "inCarts", "demandScore", "pageTitle", "url", "observedAt"]
    return _csv_response(headers, [_listing_item(listing, check, picked=pick_state == "picked")
                                   for listing, check in rows],
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


@router.get("/agents/{agent_id}/parent-keywords")
def agent_parent_keywords(
    agent_id: str, authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    agent = authenticate_agent(db, agent_id, authorization)
    return {"ok": True, **_parent_keyword_items(db, agent.tenant_id)}


@router.get("/agents/{agent_id}/blacklist")
def agent_blacklist(agent_id: str, authorization: str | None = Header(None),
                    db: Session = Depends(get_db)):
    agent = authenticate_agent(db, agent_id, authorization)
    return {"ok": True, **_blacklist_view(db, agent.tenant_id)}


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