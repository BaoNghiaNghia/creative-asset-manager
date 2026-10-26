"""Idempotent Stage 2 Products UGC Scout campaign bootstrap.

Both the API and the durable Run-all coordinator call this under a locked
source plan. Never attach a different design's existing Scout campaign.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import RrugcSourcePlanModel
from app.modules.realistic_review_ugc.service import RrugcService
from app.modules.realistic_review_ugc.source_plans import _source_campaign_name


def bootstrap_products_ugc_campaign(
    session: Session, *, plan: RrugcSourcePlanModel, user_id: str,
) -> None:
    """Create only when absent. Caller owns the DB transaction and plan lock."""
    if plan.campaign_id:
        return
    context = plan.visual_context_json if isinstance(plan.visual_context_json, dict) else {}
    phrase = str(
        context.get("quote") or context.get("text")
        or plan.source_name.rsplit(".", 1)[0]
    ).strip()[:120]
    queries = [
        (phrase + " embroidered baseball hat lifestyle").strip()[:240],
        "casual candid person wearing embroidered five panel baseball cap",
        "authentic everyday lifestyle hat portrait",
    ]
    campaign, _unused_token = RrugcService(session).create_campaign(
        tenant_id=plan.tenant_id, user_id=user_id,
        name=_source_campaign_name(plan), query=queries[0],
        search_queries=queries, target_count=20, max_scroll_batches=5,
        auto_import=False, auto_scout=True, reject_headwear=False,
        commit=False,
    )
    plan.campaign_id = campaign.id
    session.flush()
