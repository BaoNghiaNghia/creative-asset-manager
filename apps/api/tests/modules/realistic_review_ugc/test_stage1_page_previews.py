"""Stage 1 first paint uses one bounded, tenant-safe preview batch."""
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT
from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel, RrugcImageOutputVersionModel, RrugcSourcePlanModel,
)
from app.modules.realistic_review_ugc.stage1_preview_batch import stage1_page_previews


def test_page_preview_batch_limits_to_three_and_keeps_tenant_selection_private():
    engine = create_engine("sqlite://")
    for table in (
        RrugcCampaignModel.__table__, RrugcSourcePlanModel.__table__,
        RrugcImageOutputVersionModel.__table__,
    ):
        table.create(engine)
    with Session(engine) as session:
        for tenant in ("tenant-a", "tenant-b"):
            for version in range(1, 6):
                session.add(RrugcImageOutputVersionModel(
                    id=f"{tenant}-output-{version}", tenant_id=tenant,
                    stage="stage1", job_id="job-1", version=version,
                    remote_file_id=f"{tenant}-file-{version}", content_type="image/png",
                    width=2048, height=1024,
                    created_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
                ))
        # Only tenant-a, latest design v5 is picked. tenant-b has the same
        # logical job identifier but must not affect returned metadata.
        session.add(RrugcSourcePlanModel(
            id="picked-a", tenant_id="tenant-a",
            root_folder_id=PRODUCTS_STAGE1_SELECTED_ROOT,
            source_file_id="tenant-a-file-5", source_relative_path="design_05.png",
            source_name="design_05.png", source_mime_type="image/png",
            source_revision="revision-a", created_by_user_id="tester",
            status="available",
        ))
        session.commit()
        first = stage1_page_previews(
            session, tenant_id="tenant-a", job_ids=["job-1", "job-empty"],
        )
        assert [v["version"] for v in first["job-1"]] == [5, 4, 3]
        assert len(first["job-1"]) == 3
        assert "job-empty" not in first
        assert first["job-1"][0]["selected_for_products_stage2"] is True
        assert first["job-1"][1]["selected_for_products_stage2"] is False
        assert all("tenant-a" not in str(v) for v in first["job-1"])
        assert first["job-1"][0]["url"].endswith("/stage1/job-1/outputs/5")
        other = stage1_page_previews(session, tenant_id="tenant-b", job_ids=["job-1"])
        assert [v["version"] for v in other["job-1"]] == [5, 4, 3]
        assert all(v["selected_for_products_stage2"] is False for v in other["job-1"])
        assert stage1_page_previews(session, tenant_id="tenant-a", job_ids=[]) == {}
    engine.dispose()
