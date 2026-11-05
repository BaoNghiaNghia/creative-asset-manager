#!/usr/bin/env python3
"""Bounded, reversible-to-queue (not to review) TikTokShop Scout recovery.

Only old, auto-rejected near-duplicates with a new independent group context
can be re-analyzed. Never change manual Bad/AI decisions or approve results.
Preview is read-only; --apply creates at most --limit ordinary AI queue jobs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine, exists, func, select
from sqlalchemy.orm import Session

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(REPO_ROOT / "deploy" / "tools"))

from production_env import parse_environment_file
from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel, RrugcCandidateModel, RrugcSourcePlanModel,
    RrugcVisualFingerprintModel,
)
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.realistic_review_ugc.scout_automation import quality_pipeline_count
from app.modules.realistic_review_ugc.service import RrugcService
from app.modules.realistic_review_ugc.visual_dedupe import is_visual_near_duplicate
from app.modules.realistic_review_ugc.workspace import TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID


def fingerprints(candidate):
    signal = candidate.ai_signal_json if isinstance(candidate.ai_signal_json, dict) else {}
    values = signal.get("visual_fingerprints")
    return [v for v in values if isinstance(v, str) and len(v) == 64] if isinstance(values, list) else []


def recover(session: Session, *, apply: bool = False, limit: int = 16,
            per_campaign: int = 1) -> dict:
    if not 1 <= limit <= 40 or not 1 <= per_campaign <= 3:
        raise ValueError("Strictly bounded to 1-40 jobs and 1-3 per campaign")
    plan_ids = session.execute(
        select(RrugcSourcePlanModel.tenant_id, RrugcSourcePlanModel.campaign_id)
        .where(
            RrugcSourcePlanModel.root_folder_id == TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID,
            RrugcSourcePlanModel.status == "ready",
            RrugcSourcePlanModel.campaign_id.is_not(None),
        ).distinct()
    ).all()
    scope = set(plan_ids)
    if not scope:
        return {"mode": "apply" if apply else "dry_run", "requeued": 0,
                "scanned_campaigns": 0, "reason": "no_ready_tiktokshop_plans"}
    repo = RrugcRepository(session)
    service = RrugcService(session)
    tenant_ids = sorted({tenant for tenant, _ in scope})
    excluded_negative: dict[str, list[str]] = {}
    for tenant in tenant_ids:
        negative_rows = session.execute(
            select(RrugcVisualFingerprintModel.fingerprint)
            .join(RrugcCandidateModel,
                  (RrugcCandidateModel.tenant_id == RrugcVisualFingerprintModel.tenant_id)
                  & (RrugcCandidateModel.id == RrugcVisualFingerprintModel.candidate_id))
            .where(
                RrugcVisualFingerprintModel.tenant_id == tenant,
                RrugcCandidateModel.ai_signal_json["reference_manual_label"]
                    .as_string().in_(("bad", "ai")),
                exists(select(RrugcSourcePlanModel.id).where(
                    RrugcSourcePlanModel.tenant_id == tenant,
                    RrugcSourcePlanModel.campaign_id == RrugcCandidateModel.campaign_id,
                    RrugcSourcePlanModel.root_folder_id == TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID,
                )),
            )
        ).scalars().all()
        excluded_negative[tenant] = list(dict.fromkeys(negative_rows))

    campaigns = []
    for tenant, campaign_id in sorted(scope):
        row = session.get(RrugcCampaignModel, campaign_id)
        if row and row.tenant_id == tenant and row.auto_scout and row.auto_import and row.status == "running":
            counts = repo.campaign_usable_counts(tenant, campaign_id)
            pipeline = quality_pipeline_count(counts, auto_import=True)
            if pipeline < row.target_count:
                campaigns.append((pipeline, counts.get("drive_ready", 0),
                                  str(row.scan_last_started_at or ""), tenant, row))
    campaigns.sort(key=lambda item: (item[0], item[1], item[2], item[4].id))
    chosen = []
    skipped_manual_negative = 0
    skipped_group_duplicate = 0
    for pipeline, _, _, tenant, campaign in campaigns:
        if len(chosen) >= limit:
            break
        positives = repo.visual_fingerprint_rows(
            tenant, "", campaign_id=campaign.id,
        )
        group_selected: list[str] = []
        group_selected_count = 0
        candidates = session.scalars(
            select(RrugcCandidateModel).where(
                RrugcCandidateModel.tenant_id == tenant,
                RrugcCandidateModel.campaign_id == campaign.id,
                RrugcCandidateModel.status == "rejected_duplicate",
                RrugcCandidateModel.reject_reason == "visual_near_duplicate",
            ).order_by(
                RrugcCandidateModel.created_at.desc(),
                RrugcCandidateModel.id.desc(),
            ).limit(180)
        )
        for candidate in candidates:
            if group_selected_count >= per_campaign or len(chosen) >= limit:
                break
            signal = candidate.ai_signal_json if isinstance(candidate.ai_signal_json, dict) else {}
            if signal.get("reference_manual_label") in ("bad", "ai") or candidate.ai_manual_label == "ai":
                continue
            fp = fingerprints(candidate)
            if not fp:
                continue
            if is_visual_near_duplicate(fp, excluded_negative[tenant]):
                skipped_manual_negative += 1
                continue
            if is_visual_near_duplicate(fp, positives + group_selected):
                skipped_group_duplicate += 1
                continue
            chosen.append(candidate)
            group_selected.extend(fp)
            group_selected_count += 1
    planned = len(chosen)
    if apply and chosen:
        for candidate in chosen:
            service.enqueue_analysis(candidate, increment_revision=True)
        session.commit()
    result = {
        "mode": "apply" if apply else "dry_run",
        "requeued": planned if apply else 0,
        "eligible_preview": planned,
        "scanned_campaigns": len(campaigns),
        "campaigns_selected": len({item.campaign_id for item in chosen}),
        "manual_negative_anchors": sum(len(v) for v in excluded_negative.values()),
        "skipped_manual_negative": skipped_manual_negative,
        "skipped_group_duplicate": skipped_group_duplicate,
    }
    if not apply:
        session.rollback()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Enqueue bounded re-analysis")
    parser.add_argument("--limit", type=int, default=16)
    parser.add_argument("--per-campaign", type=int, default=1)
    parser.add_argument("--env-file",
                        default="/etc/creative-asset-manager/production.env")
    args = parser.parse_args()
    env = parse_environment_file(Path(args.env_file))
    engine = create_engine(env["DATABASE_URL"], pool_pre_ping=True)
    try:
        with Session(engine, expire_on_commit=False) as session:
            result = recover(session, apply=args.apply, limit=args.limit,
                             per_campaign=args.per_campaign)
        print(json.dumps(result, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
