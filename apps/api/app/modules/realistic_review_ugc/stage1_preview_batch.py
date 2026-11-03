"""Bounded Stage 1 first-paint previews, fetched alongside the keyword page.

A single tenant-scoped SQL window query replaces one authenticated version-list
request per visible table row. Full output history is still fetched on demand.
Never expose remote storage identifiers to the browser.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT
from app.modules.realistic_review_ugc.model import (
    RrugcImageOutputVersionModel, RrugcSourcePlanModel,
)


def stage1_page_previews(
    session: Session, *, tenant_id: str, job_ids: list[str], limit_per_job: int = 3,
) -> dict[str, list[dict]]:
    if not job_ids:
        return {}
    if not 1 <= limit_per_job <= 3:
        raise ValueError("Stage 1 first-paint preview limit must be between 1 and 3")
    ids = list(dict.fromkeys(job_ids))
    model = RrugcImageOutputVersionModel
    ranked = select(
        model.job_id.label("job_id"),
        model.version.label("version"),
        model.remote_file_id.label("remote_file_id"),
        model.width.label("width"),
        model.height.label("height"),
        model.output_name.label("output_name"),
        model.created_at.label("created_at"),
        func.row_number().over(
            partition_by=model.job_id,
            order_by=model.version.desc(),
        ).label("preview_rank"),
    ).where(
        model.tenant_id == tenant_id,
        model.stage == "stage1",
        model.job_id.in_(ids),
    ).subquery()
    versions = session.execute(
        select(
            ranked.c.job_id, ranked.c.version, ranked.c.remote_file_id,
            ranked.c.width, ranked.c.height,
            ranked.c.output_name, ranked.c.created_at,
        ).where(ranked.c.preview_rank <= limit_per_job)
        .order_by(ranked.c.job_id, ranked.c.version.desc())
    ).all()
    if not versions:
        return {}
    remote_ids = {item.remote_file_id for item in versions}
    selected = set(session.scalars(
        select(RrugcSourcePlanModel.source_file_id).where(
            RrugcSourcePlanModel.tenant_id == tenant_id,
            RrugcSourcePlanModel.root_folder_id == PRODUCTS_STAGE1_SELECTED_ROOT,
            RrugcSourcePlanModel.status != "missing",
            RrugcSourcePlanModel.source_file_id.in_(remote_ids),
        )
    ))
    previews: dict[str, list[dict]] = defaultdict(list)
    for item in versions:
        previews[item.job_id].append({
            "version": item.version,
            "url": ("/api/v1/realistic-review-ugc/generation-jobs/stage1/"
                    + item.job_id + "/outputs/" + str(item.version)),
            "width": item.width,
            "height": item.height,
            "output_name": item.output_name,
            "created_at": item.created_at.isoformat(),
            "selected_for_products_stage2": item.remote_file_id in selected,
        })
    return dict(previews)
