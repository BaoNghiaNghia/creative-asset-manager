"""Products Slot 6 verified Fast Fill: tenant isolation, feedback and idempotency."""
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT
from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel, RrugcCandidateModel, RrugcSourcePlanModel,
)
from app.modules.realistic_review_ugc.service import RrugcService
from app.modules.realistic_review_ugc.ugc_reference_fast_fill import warm_start_products_ugc
from app.modules.realistic_review_ugc.scout_automation import (
    scout_client_version_number, scout_client_supports_source_plans,
)

def test_windows_scout_version_suffix_keeps_product_run_all_priority():
    assert scout_client_version_number("rrugc-scout-v58-tiktokshop-isolation") == 58
    assert scout_client_version_number("rrugc-scout-v59") == 59
    assert scout_client_supports_source_plans("rrugc-scout-v58-tiktokshop-isolation")
    assert not scout_client_supports_source_plans("rrugc-scout-v11")
    assert scout_client_version_number("rrugc-scout-vx") is None

from app.modules.realistic_review_ugc.ugc_verified_reuse import load_products_ugc_references
from app.modules.realistic_review_ugc.ugc_scene_selection import eligible_ugc_references
from app.modules.realistic_review_ugc.workspace import TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID


def campaign(db, tenant, name):
    c, _token = RrugcService(db).create_campaign(
        tenant_id=tenant, user_id="operator", name=name,
        query="person wearing cap candid", target_count=20,
        max_scroll_batches=3, auto_import=False,
    )
    return c


def source(db, campaign_id, tenant, index, *, verified=True, good=True):
    row = RrugcCandidateModel(
        tenant_id=tenant, campaign_id=campaign_id,
        source_key=f"source_{campaign_id}_{index}",
        pin_url=f"https://www.pinterest.com/pin/{campaign_id}{index}/",
        image_url=f"https://i.pinimg.com/736x/ugc-{campaign_id}-{index}.jpg",
        status="approved" if verified else "needs_review",
        content_hash=f"hash_{campaign_id}_{index}",
        head_visible=True, people_count=1, mobile_ugc_score=.88,
        final_score=.82 if good else .32, ai_signal_json={},
    )
    db.add(row)
    return row


def test_fast_fill_reuses_20_already_verified_gator_refs_and_replenishes_after_reject():
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    for table in (RrugcCampaignModel.__table__, RrugcSourcePlanModel.__table__,
                  RrugcCandidateModel.__table__):
        table.create(engine)
    with Session(engine) as db:
        target = campaign(db, "tenant-a", "Design-specific")
        other = campaign(db, "tenant-a", "Gator reviewed")
        tik = campaign(db, "tenant-a", "TikTok ignored")
        foreign = campaign(db, "tenant-b", "Foreign ignored")
        plan = RrugcSourcePlanModel(
            tenant_id="tenant-a", root_folder_id=PRODUCTS_STAGE1_SELECTED_ROOT,
            source_file_id="design", source_name="design.png",
            source_relative_path="design.png", source_revision="rev1",
            source_mime_type="image/png", created_by_user_id="operator",
            status="ready", campaign_id=target.id, embroidery_signature="design-a",
        )
        db.add(plan)
        db.add(RrugcSourcePlanModel(
            tenant_id="tenant-a", root_folder_id=TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID,
            source_file_id="tik", source_name="tik.png",
            source_relative_path="tik.png", source_revision="tik-rev",
            source_mime_type="image/png", created_by_user_id="operator",
            campaign_id=tik.id, status="ready"))
        # 28 real Gator, 10 TikTok, 10 foreign: strictly source-domain scoped.
        for i in range(28):
            source(db, other.id, "tenant-a", i, good=i != 0)
        for i in range(10):
            source(db, tik.id, "tenant-a", i)
            source(db, foreign.id, "tenant-b", i)
        db.commit()

        first = warm_start_products_ugc(db, plan=plan, campaign=target)
        db.commit()
        assert first == {"eligible": 20, "created": 20}
        assert target.status == "completed"
        again = warm_start_products_ugc(db, plan=plan, campaign=target)
        assert again == {"eligible": 20, "created": 0}
        proxies = list(db.scalars(select(RrugcCandidateModel).where(
            RrugcCandidateModel.campaign_id == target.id,
        )))
        assert len(proxies) == 20
        assert all(c.status == "rejected_duplicate"
                   and c.ai_signal_json.get("ugc_fast_fill") == "verified_same_tenant_v1"
                   for c in proxies)
        foreign_ids = {r.id for r in db.scalars(select(RrugcCandidateModel).where(
            RrugcCandidateModel.campaign_id.in_((tik.id, foreign.id)),
        ))}
        assert all(c.ai_signal_json["duplicate_exact_candidate_id"] not in foreign_ids
                   for c in proxies)
        assert all(c.image_url.startswith("https://i.pinimg.com/")
                   for c in proxies)

        # A vote only excludes the user's local copy and triggers replacement.
        rejected = proxies[0]
        rejected.ai_signal_json = {**rejected.ai_signal_json,
                                   "reference_manual_label": "bad"}
        db.commit()
        after_reject = warm_start_products_ugc(db, plan=plan, campaign=target)
        db.commit()
        assert after_reject == {"eligible": 20, "created": 1}
        assert len(eligible_ugc_references(load_products_ugc_references(
            db, tenant_id="tenant-a", campaign_id=target.id))) == 20
        assert rejected.ai_signal_json["reference_manual_label"] == "bad"
    engine.dispose()


def test_fast_fill_preserves_scout_when_verified_pool_cannot_reach_target():
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    for table in (RrugcCampaignModel.__table__, RrugcSourcePlanModel.__table__,
                  RrugcCandidateModel.__table__):
        table.create(engine)
    with Session(engine) as db:
        target = campaign(db, "tenant", "Target")
        peer = campaign(db, "tenant", "Peer")
        plan = RrugcSourcePlanModel(
            tenant_id="tenant", root_folder_id=PRODUCTS_STAGE1_SELECTED_ROOT,
            source_file_id="design", source_name="design.png",
            source_relative_path="design.png", source_revision="rev",
            source_mime_type="image/png", created_by_user_id="operator",
            campaign_id=target.id, status="ready",
        )
        db.add(plan)
        for i in range(4):
            source(db, peer.id, "tenant", i)
        db.commit()
        result = warm_start_products_ugc(db, plan=plan, campaign=target)
        db.commit()
        assert result == {"eligible": 4, "created": 4}
        assert target.status == "running"
        assert target.auto_scout is True
        assert target.scan_next_at is not None
    engine.dispose()
