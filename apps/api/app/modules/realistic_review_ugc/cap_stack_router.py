"""Tenant-isolated shared Cap Stack library and separately leased Scout lane.

This intentionally never creates campaigns/source plans or writes Stage 3
candidate feedback. The auto label/count vision classification is NOT inferred
from a Pinterest title: unreviewed images remain pending until verified.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import random
import re
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.realistic_review_ugc.cap_stack_model import RrugcCapStackControlModel, RrugcCapStackImageModel
from app.modules.realistic_review_ugc.cap_stack_analysis import (
    CAP_STACK_MIN_CAPS, CAP_STACK_MAX_CAPS, enqueue_cap_stack_pending,
)
from app.modules.realistic_review_ugc.scout_automation import RrugcAutoScoutService
from app.modules.realistic_review_ugc.service import RrugcError

router = APIRouter(prefix="/cap-stack", tags=["realistic-review-ugc-cap-stack"])
READ = require_permission("realistic_review_ugc.read")
RUN = require_permission("realistic_review_ugc.run")
LANE_TARGET = 12
LEASE_MINUTES = 45
# Search alternatives must genuinely change between leases as human votes arrive.
# Keep Cap Stack as the canonical anchor, but never keep searching only its
# original four synonyms after the user approves/rejects references.
SCOUT_SEARCHES = (
    "Cap Stack", "baseball cap stack", "stacked trucker hats",
    "pile of baseball caps", "two baseball caps stacked",
    "three baseball caps stacked", "vintage baseball caps stacked",
    "embroidered baseball caps stack", "colorful baseball hats pile",
    "snapback baseball caps stacked", "stack of hats on wooden table",
    "stacked baseball caps on shelf", "retro baseball caps stack",
    "baseball cap pile studio photograph",
)
SCOUT_STYLES = {
    "vintage": ("vintage", "retro", "washed", "distressed"),
    "embroidered": ("embroider", "stitched", "embroidery", "logo"),
    "colorful": ("colorful", "colourful", "bright", "multicolor", "multi color"),
    "snapback": ("snapback", "flat brim"),
    "trucker": ("trucker", "mesh"),
    "wooden": ("wood", "wooden", "tabletop"),
    "shelf": ("shelf", "rack", "display"),
    "studio": ("studio", "isolated", "white background"),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt and dt.tzinfo is None else dt


def pin_key(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").casefold().rstrip(".")
    if parts.scheme != "https" or host not in {"www.pinterest.com", "pinterest.com"}:
        raise HTTPException(422, "Pinterest HTTPS pin link required.")
    match = re.fullmatch(r"/pin/(\d+)/?", parts.path)
    if not match:
        raise HTTPException(422, "Pinterest pin numeric ID required.")
    return match.group(1)


def validate_image_url(url: str) -> None:
    parts = urlsplit(url)
    host = (parts.hostname or "").casefold().rstrip(".")
    if parts.scheme != "https" or not (host == "pinimg.com" or host.endswith(".pinimg.com")):
        raise HTTPException(422, "Pinterest pinimg HTTPS image required.")
    if parts.username or parts.password or parts.port or not parts.path:
        raise HTTPException(422, "Unexpected Pinterest image URL.")


def pinimg_asset_key(url: str) -> str:
    """Compare the same Pin image across 236x/736x/originals and different Pin IDs."""
    parsed = urlsplit(str(url or ""))
    if parsed.scheme != "https" or (parsed.hostname or "").casefold().rstrip(".") not in {
        "i.pinimg.com", "pinimg.com",
    }:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and re.fullmatch(r"(?:originals|[0-9]+x(?:[0-9]+)?(?:_[A-Za-z0-9]+)?)", parts[0], flags=re.I):
        parts = parts[1:]
    return "/".join(parts).casefold()


def get_control(session: Session, tenant: str, *, lock: bool = False):
    stmt = select(RrugcCapStackControlModel).where(RrugcCapStackControlModel.tenant_id == tenant)
    return session.scalar(stmt.with_for_update() if lock else stmt)


def agent_auth(session: Session, agent_id: str, authorization: str | None):
    parts = str(authorization or "").split(" ", 1)
    if len(parts) != 2 or parts[0].casefold() != "bearer" or not parts[1].strip():
        raise HTTPException(401, "Scout token required.")
    try:
        return RrugcAutoScoutService(session).authenticate_agent(
            agent_id=agent_id, raw_token=parts[1].strip(),
        )
    except RrugcError:
        raise HTTPException(401, "Invalid Scout credentials.") from None


def image_dict(row: RrugcCapStackImageModel) -> dict:
    return {
        "id": row.id, "pin_url": row.pin_url, "image_url": row.image_url,
        "alt_text": row.alt_text, "source_query": row.source_query,
        "caps_detected": row.caps_detected, "count_status": row.count_status,
        "count_confidence": row.count_confidence,
        "is_cap_stack": row.is_cap_stack, "ai_suspected": row.ai_suspected,
        "ai_suspicion_confidence": row.ai_suspicion_confidence,
        "visual_summary": row.visual_summary,
        "analysis_model": row.analysis_model,
        "analysis_error_code": row.analysis_error_code,
        "analyzed_at": row.analyzed_at,
        "manual_caps_count": row.manual_caps_count, "manual_label": row.manual_label,
        "created_at": row.created_at,
    }


class SettingsRequest(BaseModel):
    enabled: bool


class FeedbackRequest(BaseModel):
    label: str | None = Field(default=None, pattern="^(good|bad|ai|clear)$")
    caps_count: int | None = Field(default=None, ge=CAP_STACK_MIN_CAPS, le=CAP_STACK_MAX_CAPS)


class ScoutCandidate(BaseModel):
    pin_url: str = Field(min_length=18, max_length=2048)
    image_url: str = Field(min_length=15, max_length=4096)
    alt_text: str | None = Field(default=None, max_length=2000)


class ScoutBatch(BaseModel):
    items: list[ScoutCandidate] = Field(min_length=1, max_length=24)
    source_query: str | None = Field(default=None, max_length=256)


class CompleteRequest(BaseModel):
    status: str = Field(pattern="^(completed|failed|needs_login)$")
    error_code: str | None = Field(default=None, max_length=100)


class HeartbeatRequest(BaseModel):
    status: str = Field(default="busy")


@router.get("")
def list_cap_stack(
    q: str = Query(default="", max_length=160),
    label: str = Query(default="all", pattern="^(all|pending|good|bad|ai)$"),
    caps_count: int | None = Query(default=None, ge=1, le=50),
    vision_status: str = Query(default="all", pattern="^(all|pending|analyzing|ready|needs_review|not_cap_stack|out_of_range|error)$"),
    page: int = Query(default=1, ge=1, le=10000),
    page_size: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    tenant = principal.active_tenant_id
    stmt = select(RrugcCapStackImageModel).where(RrugcCapStackImageModel.tenant_id == tenant)
    if q.strip():
        escaped = q.strip().replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(
            RrugcCapStackImageModel.alt_text.ilike("%" + escaped + "%", escape="\\")
            | RrugcCapStackImageModel.source_query.ilike("%" + escaped + "%", escape="\\")
        )
    if label == "pending":
        stmt = stmt.where(RrugcCapStackImageModel.manual_label.is_(None))
    elif label != "all":
        stmt = stmt.where(RrugcCapStackImageModel.manual_label == label)
    if isinstance(vision_status, str) and vision_status != "all":
        stmt = stmt.where(RrugcCapStackImageModel.count_status == vision_status)
    # "Any number" means any VERIFIED/DETECTED count from 2 to 6, never
    # unresolved, single-cap, or out-of-range images. Manual corrections
    # override Gemini counts for both the range and individual filters.
    effective_count = func.coalesce(
        RrugcCapStackImageModel.manual_caps_count,
        RrugcCapStackImageModel.caps_detected,
    )
    if caps_count is None:
        stmt = stmt.where(effective_count.between(CAP_STACK_MIN_CAPS, CAP_STACK_MAX_CAPS))
    else:
        stmt = stmt.where(effective_count == caps_count)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(session.scalars(
        stmt.order_by(RrugcCapStackImageModel.created_at.desc(), RrugcCapStackImageModel.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ))
    totals = Counter(
        row or "pending" for row in session.scalars(
            select(RrugcCapStackImageModel.manual_label).where(
                RrugcCapStackImageModel.tenant_id == tenant
            )
        )
    )
    control = get_control(session, tenant)
    vision_counts = Counter(
        status for status in session.scalars(
            select(RrugcCapStackImageModel.count_status).where(
                RrugcCapStackImageModel.tenant_id == tenant
            )
        )
    )
    suspected_ai = int(session.scalar(select(func.count(RrugcCapStackImageModel.id)).where(
        RrugcCapStackImageModel.tenant_id == tenant,
        RrugcCapStackImageModel.ai_suspected.is_(True),
    )) or 0)
    return {
        "vision": {
            "ready": vision_counts["ready"], "analyzing": vision_counts["analyzing"],
            "pending": vision_counts["pending"], "needs_review": vision_counts["needs_review"],
            "not_cap_stack": vision_counts["not_cap_stack"],
            "out_of_range": vision_counts["out_of_range"], "error": vision_counts["error"],
            "ai_suspected": suspected_ai,
        },
        "items": [image_dict(row) for row in rows], "total": total,
        "page": page, "page_size": page_size,
        "stats": {"discovered": sum(totals.values()), "approved": totals["good"],
                  "rejected": totals["bad"], "ai": totals["ai"], "pending": totals["pending"]},
        "scout": {
            "enabled": bool(control.enabled) if control else False,
            "running": bool(control and control.lease_run_id
                            and utc_aware(control.lease_expires_at) and utc_aware(control.lease_expires_at) > utc_now()),
            "last_result": control.last_result if control else None,
            "next_scan_at": control.next_scan_at if control else None,
        },
    }


@router.post("/settings")
def set_cap_stack_settings(
    body: SettingsRequest, session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    tenant = principal.active_tenant_id
    control = get_control(session, tenant, lock=True)
    if control is None:
        control = RrugcCapStackControlModel(tenant_id=tenant, enabled=body.enabled)
        session.add(control)
    else:
        control.enabled = body.enabled
    if not body.enabled:
        control.lease_run_id = None
        control.lease_agent_id = None
        control.lease_expires_at = None
    else:
        control.next_scan_at = None
    session.commit()
    return {"enabled": control.enabled}


@router.post("/{image_id}/feedback")
def set_cap_stack_feedback(
    image_id: str, body: FeedbackRequest, session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    row = session.scalar(select(RrugcCapStackImageModel).where(
        RrugcCapStackImageModel.tenant_id == principal.active_tenant_id,
        RrugcCapStackImageModel.id == image_id,
    ).with_for_update())
    if row is None:
        raise HTTPException(404, "Cap Stack image not found.")
    if body.label == "good":
        verified_count = body.caps_count if body.caps_count is not None else (
            row.manual_caps_count if row.manual_caps_count is not None else row.caps_detected
        )
        if verified_count is None or not CAP_STACK_MIN_CAPS <= verified_count <= CAP_STACK_MAX_CAPS:
            raise HTTPException(422, "Approve only Cap Stack images with 2–6 visible caps. Verify the cap count first.")
    if body.label is not None:
        row.manual_label = None if body.label == "clear" else body.label
        row.reviewed_at = utc_now()
        row.feedback_by = str(getattr(principal, "user_id", "") or "")
    if body.caps_count is not None:
        row.manual_caps_count = body.caps_count
    session.commit()
    return image_dict(row)



@router.post("/{image_id}/analyze")
def retry_cap_stack_vision(
    image_id: str, session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    """Reanalyze a photo while preserving human labels and count overrides."""
    tenant = principal.active_tenant_id
    row = session.scalar(select(RrugcCapStackImageModel).where(
        RrugcCapStackImageModel.tenant_id == tenant,
        RrugcCapStackImageModel.id == image_id,
    ).with_for_update())
    if row is None:
        raise HTTPException(404, "Cap Stack image not found.")
    if row.count_status in ("analyzing", "pending"):
        raise HTTPException(409, "Cap Stack vision is already pending.")
    row.analysis_revision += 1
    row.count_status = "pending"
    row.analysis_error_code = None
    row.analyzed_at = None
    session.flush()
    queued = enqueue_cap_stack_pending(session, tenant, limit=6)
    session.commit()
    return {"image": image_dict(row), "vision_queued": queued}


def _cap_stack_style_words(text: str | None) -> set[str]:
    lowered = str(text or "").casefold()
    return {
        style for style, keywords in SCOUT_STYLES.items()
        if any(term in lowered for term in keywords)
    }


def cap_stack_learning_queries(session: Session, tenant: str) -> list[str]:
    """Choose distinct queries from *both* positive and negative image votes.

    Previous implementation always sent the same four search strings: sorting
    all four cannot teach Pinterest what to stop discovering.
    """
    rows = list(session.execute(select(
        RrugcCapStackImageModel.source_query,
        RrugcCapStackImageModel.manual_label,
        RrugcCapStackImageModel.alt_text,
        RrugcCapStackImageModel.visual_summary,
    ).where(
        RrugcCapStackImageModel.tenant_id == tenant,
        RrugcCapStackImageModel.manual_label.in_(("good", "bad", "ai")),
    ).order_by(RrugcCapStackImageModel.reviewed_at.desc()).limit(1500)))
    source_votes: dict[str, list[int]] = {}
    style_votes: dict[str, list[int]] = {}
    for source, label, alt, summary in rows:
        index = 0 if label == "good" else 1
        source_votes.setdefault(str(source or "").strip().casefold(), [0, 0])[index] += 1
        for style in _cap_stack_style_words(" ".join((alt or "", summary or ""))):
            style_votes.setdefault(style, [0, 0])[index] += 1

    def value(query: str) -> float:
        positive, negative = source_votes.get(query.casefold(), [0, 0])
        score = (positive + 1) / (positive + negative + 2)
        for style in _cap_stack_style_words(query):
            good, bad = style_votes.get(style, [0, 0])
            # Mild priors prevent one reject from eliminating a category;
            # repeated rejection pushes that style to the bottom.
            score += (good * .12 - bad * .18) / (good + bad + 2)
        return score

    ranked = sorted(
        (q for q in SCOUT_SEARCHES if q != "Cap Stack"),
        key=lambda q: (-value(q), SCOUT_SEARCHES.index(q)),
    )
    # Two exploitation queries plus a deterministic daily exploration slot.
    # Even with no votes, the discovery families rotate instead of repeating
    # the same four Pinterest searches forever.
    best = ranked[:2]
    unexplored = [q for q in SCOUT_SEARCHES[1:] if q not in best]
    rotation = (utc_now().date().toordinal() + sum(tenant.encode("utf-8"))) % len(unexplored)
    return ["Cap Stack", *best, unexplored[rotation]]


def control_for_run(session: Session, tenant: str, agent_id: str, run_id: str):
    row = get_control(session, tenant, lock=True)
    if (not row or not row.enabled or row.lease_agent_id != agent_id
            or row.lease_run_id != run_id or not utc_aware(row.lease_expires_at)
            or utc_aware(row.lease_expires_at) <= utc_now()):
        raise HTTPException(409, "Cap Stack lease missing, expired or disabled.")
    return row


@router.post("/scout-agents/{agent_id}/claim")
def claim_cap_stack(
    agent_id: str, authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    agent = agent_auth(session, agent_id, authorization)
    control = get_control(session, agent.tenant_id, lock=True)
    now = utc_now()
    if (not control or not control.enabled
            or (utc_aware(control.next_scan_at) and utc_aware(control.next_scan_at) > now)
            or (utc_aware(control.lease_expires_at) and utc_aware(control.lease_expires_at) > now)):
        return None
    control.lease_agent_id = agent_id
    control.lease_run_id = str(uuid4())
    control.lease_expires_at = now + timedelta(minutes=LEASE_MINUTES)
    control.run_created = 0
    session.commit()
    rows = list(session.scalars(select(RrugcCapStackImageModel.pin_url).where(
        RrugcCapStackImageModel.tenant_id == agent.tenant_id
    ).order_by(RrugcCapStackImageModel.created_at.desc()).limit(3500)))
    seed_rows = list(session.scalars(select(RrugcCapStackImageModel).where(
        RrugcCapStackImageModel.tenant_id == agent.tenant_id,
        RrugcCapStackImageModel.manual_label == "good",
    ).order_by(RrugcCapStackImageModel.reviewed_at.desc()).limit(8)))
    # Historical rejects automatically become an exclusion memory for the
    # NEXT claim, without requiring the user to vote on the same images again.
    rejected_rows = list(session.scalars(select(RrugcCapStackImageModel).where(
        RrugcCapStackImageModel.tenant_id == agent.tenant_id,
        RrugcCapStackImageModel.manual_label.in_(("bad", "ai")),
    ).order_by(RrugcCapStackImageModel.reviewed_at.desc()).limit(120)))
    return {
        "run": {"id": control.lease_run_id}, "campaign_id": "cap-stack-shared", "scout_type": "cap_stack",
        "query": "Cap Stack", "search_queries": cap_stack_learning_queries(session, agent.tenant_id),
        "target_count": LANE_TARGET, "max_scroll_batches": 6,
        "progress": 0, "pipeline_count": 0, "source_context": None,
        "known_pin_urls": rows,
        "related_seeds": [{"pin_url": row.pin_url, "image_url": row.image_url,
                            "alt_text": row.alt_text} for row in seed_rows],
        "rejected_seeds": [{"pin_url": row.pin_url, "image_url": row.image_url,
                            "alt_text": row.alt_text,
                            "visual_summary": row.visual_summary,
                            "label": row.manual_label} for row in rejected_rows],
    }


@router.post("/scout-agents/{agent_id}/runs/{run_id}/heartbeat")
def heartbeat_cap_stack(
    agent_id: str, run_id: str, body: HeartbeatRequest,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    agent = agent_auth(session, agent_id, authorization)
    control = control_for_run(session, agent.tenant_id, agent_id, run_id)
    control.lease_expires_at = utc_now() + timedelta(minutes=LEASE_MINUTES)
    session.commit()
    return {"ok": True}


@router.post("/scout-agents/{agent_id}/runs/{run_id}/candidates")
def submit_cap_stack(
    agent_id: str, run_id: str, body: ScoutBatch,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    agent = agent_auth(session, agent_id, authorization)
    control = control_for_run(session, agent.tenant_id, agent_id, run_id)
    created = 0
    existing = 0
    feedback_excluded = 0
    # Protect even older Scout clients that do not understand rejected_seeds.
    # This rejects the SAME image re-pinned under a different numeric Pin ID,
    # including 236x/736x/originals CDN renditions. Tenant-scoped and bounded
    # by existing human-rejected rows, not a full image-library scan.
    rejected_assets = {
        pinimg_asset_key(url) for url in session.scalars(select(
            RrugcCapStackImageModel.image_url,
        ).where(
            RrugcCapStackImageModel.tenant_id == agent.tenant_id,
            RrugcCapStackImageModel.manual_label.in_(("bad", "ai")),
        ))
    }
    rejected_assets.discard("")
    seen_keys: set[str] = set()
    for item in body.items:
        key = pin_key(item.pin_url)
        validate_image_url(item.image_url)
        if key in seen_keys:
            existing += 1
            continue
        seen_keys.add(key)
        if session.scalar(select(RrugcCapStackImageModel.id).where(
            RrugcCapStackImageModel.tenant_id == agent.tenant_id,
            RrugcCapStackImageModel.pin_key == key,
        )):
            existing += 1
            continue
        if pinimg_asset_key(item.image_url) in rejected_assets:
            feedback_excluded += 1
            continue
        session.add(RrugcCapStackImageModel(
            id=str(uuid4()), tenant_id=agent.tenant_id,
            pin_key=key, pin_url=item.pin_url, image_url=item.image_url,
            alt_text=item.alt_text, source_query=body.source_query,
            discovered_by_agent_id=agent_id, count_status="pending",
        ))
        created += 1
    control.run_created += created
    control.lease_expires_at = utc_now() + timedelta(minutes=LEASE_MINUTES)
    session.flush()
    queued = enqueue_cap_stack_pending(session, agent.tenant_id)
    session.commit()
    return {
        "created": created, "existing": existing, "feedback_excluded": feedback_excluded,
        "vision_queued": queued, "progress": control.run_created,
        "pipeline_count": control.run_created, "target_count": LANE_TARGET,
        "campaign_status": "running" if control.run_created < LANE_TARGET else "complete",
    }


@router.post("/scout-agents/{agent_id}/runs/{run_id}/complete")
def complete_cap_stack(
    agent_id: str, run_id: str, body: CompleteRequest,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    agent = agent_auth(session, agent_id, authorization)
    control = control_for_run(session, agent.tenant_id, agent_id, run_id)
    control.lease_run_id = None
    control.lease_agent_id = None
    control.lease_expires_at = None
    control.last_result = body.status
    # A failed or login-blocked Pinterest session must cool down, not thrash.
    pause = 900 if body.status == "completed" else 1800
    control.next_scan_at = utc_now() + timedelta(seconds=pause)
    session.commit()
    return {"status": body.status, "created": control.run_created}


@router.get("/scout-agents/{agent_id}/status")
def cap_stack_agent_status(
    agent_id: str, authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    agent = agent_auth(session, agent_id, authorization)
    control = get_control(session, agent.tenant_id)
    return {
        "enabled": bool(control and control.enabled),
        "lease_active": bool(control and utc_aware(control.lease_expires_at) and utc_aware(control.lease_expires_at) > utc_now()),
        "last_result": control.last_result if control else None,
        "next_scan_at": control.next_scan_at if control else None,
    }