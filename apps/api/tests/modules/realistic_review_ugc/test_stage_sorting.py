from types import SimpleNamespace

from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel, RrugcSourcePlanModel, RrugcStage2JobModel,
)
from app.modules.realistic_review_ugc.router import get_source_plans
from app.modules.realistic_review_ugc.source_plans import RRUGC_SOURCE_ROOT_FOLDER_ID
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.realistic_review_ugc.model import RrugcCandidateModel

def make_database():
    engine = create_engine("sqlite://")
    for model in (TenantProcessingPolicyModel, ProcessingJobModel,
                  RrugcCampaignModel, RrugcCandidateModel,
                  RrugcSourcePlanModel, RrugcStage2JobModel):
        model.__table__.create(engine)
    return sessionmaker(engine, expire_on_commit=False)


def test_stage4_active_and_output_sort_is_global_before_pagination():
    factory = make_database()
    with factory() as session:
        for name in ("Alpha", "Beta", "Gamma"):
            campaign = RrugcCampaignModel(
                tenant_id="tenant-a", name=name, query=name,
                scout_token_hash=name.lower().ljust(64, "0"),
                auto_import=name != "Alpha",
                created_by_user_id="owner",
            )
            session.add(campaign)
            session.flush()
            plan = RrugcSourcePlanModel(
                tenant_id="tenant-a", root_folder_id=RRUGC_SOURCE_ROOT_FOLDER_ID,
                source_file_id="source-" + name,
                source_relative_path=name + "/front.png",
                source_name=name + ".png", source_mime_type="image/png",
                source_revision=name.lower(), status="ready",
                campaign_id=campaign.id,
                visual_context_json={"embroidery_text": [name]},
                created_by_user_id="owner",
            )
            session.add(plan)
            session.flush()
            for j in range({"Alpha": 5, "Beta": 2, "Gamma": 4}[name]):
                session.add(RrugcCandidateModel(
                    tenant_id="tenant-a", campaign_id=campaign.id,
                    source_key=name + "-ref-" + str(j),
                    pin_url="https://pinterest.com/pin/" + name + str(j),
                    image_url="https://example.test/" + name + str(j) + ".jpg",
                    status="approved" if name == "Alpha" else "drive_ready",
                ))
            for i in range({"Alpha": 1, "Beta": 3, "Gamma": 2}[name]):
                # Gamma contains most active jobs but Beta most completed.
                job_status = ("queued" if name == "Gamma" else
                              "completed" if name == "Beta" else "running")
                session.add(RrugcStage2JobModel(
                    tenant_id="tenant-a", source_plan_id=plan.id,
                    campaign_id=campaign.id, source_revision=plan.source_revision,
                    skill_name="test", selected_candidate_ids_json=[],
                    selected_reference_snapshot_json=[],
                    status=job_status, idempotency_key=name + str(i),
                    created_by_user_id="owner",
                ))
        session.commit()
        principal = SimpleNamespace(active_tenant_id="tenant-a")
        def page(sort_by, page_num):
            return get_source_plans(
                page=page_num, page_size=1, q=None,
                sort_by=sort_by, sort_dir="desc",
                session=session, principal=principal,
            )
        assert page("active_jobs", 1).items[0].source_name == "Gamma.png"
        assert page("active_jobs", 2).items[0].source_name == "Alpha.png"
        assert page("outputs", 1).items[0].source_name == "Beta.png"
        assert page("outputs", 2).items[0].source_name in ("Alpha.png", "Gamma.png")
        assert page("outputs", 1).total == 3
        assert page("progress", 1).items[0].source_name == "Alpha.png"
        assert page("progress", 2).items[0].source_name == "Gamma.png"
        assert page("refs", 1).items[0].source_name == "Gamma.png"
        assert page("refs", 2).items[0].source_name == "Beta.png"
