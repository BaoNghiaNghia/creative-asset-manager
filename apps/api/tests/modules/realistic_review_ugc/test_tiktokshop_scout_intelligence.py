"""TikTokShop source-specific Scout queries, feedback isolation and dedupe."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.realistic_review_ugc.apparel_placement import SourceApparelPlacement
from app.modules.realistic_review_ugc.keyword_strategy import campaign_learning_intent
from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel, RrugcSourcePlanModel, RrugcVisualFingerprintModel,
)
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.realistic_review_ugc.schema import CandidateSubmission
from app.modules.realistic_review_ugc.service import RrugcService
from app.modules.realistic_review_ugc.tiktokshop_scout_intent import (
    rank_tiktokshop_queries_from_feedback,
    source_apparel_intent, tiktokshop_semantic_queries,
)
from app.modules.realistic_review_ugc.workspace import (
    RRUGC_SOURCE_ROOT_FOLDER_ID, TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID,
)


PLACEMENTS = [
    SourceApparelPlacement(zone="center_chest", priority="primary", confidence=0.99),
    SourceApparelPlacement(zone="left_sleeve", priority="secondary", confidence=0.96),
]


def test_product_specific_semantics_replace_shared_sweatshirt_searches():
    cases = [
        ("custom-embroidered-girl-mama-sweatshirt.jpg", "girl_mama", "mother with daughters"),
        ("custom-embroidered-godmother-sweatshirt.jpg", "godmother", "adult woman"),
        ("custom-embroidered-grandma-name-sweatshirt.jpg", "grandma", "grandmother"),
        ("custom-embroidered-in-my-engaged-era-sweatshirt.jpg", "engaged", "engaged woman"),
        ("custom-embroidered-in-my-mom-era-sweatshirt.jpg", "motherhood", "mother with children"),
        ("custom-embroidered-mama-goose-sweatshirt.jpg", "mama_goose", "adult woman"),
        ("custom-embroidered-mr-and-mrs-varsity-sweatshirt.jpg", "couple_wedding", "newlywed couple"),
        ("custom-embroidered-mrs-last-name-sweatshirt.jpg", "bride", "newlywed woman"),
    ]
    first_queries = set()
    for filename, family, persona in cases:
        snapshot = {
            "name": filename, "product_type": "sweatshirt",
            "source_category": "embroidered apparel",
        }
        intent = source_apparel_intent(snapshot)
        assert intent.family == family, filename
        assert campaign_learning_intent(
            campaign_id=filename, product_snapshot=snapshot,
        ) == "apparel:" + family
        queries = tiktokshop_semantic_queries(
            "sweatshirt", PLACEMENTS, snapshot, None,
            scan_attempt_count=1,
        )
        assert queries and len(queries) == 4
        assert all("sweatshirt" in query for query in queries)
        assert all("shirt" not in query.replace("sweatshirt", "") for query in queries)
        assert persona in queries[0]
        assert "chest and sleeves visible" in queries[0]
        first_queries.add(queries[0])
    assert len(first_queries) == len(cases), "Different products must not share generic Scout lanes"


def test_visual_embroidery_text_can_recover_semantics_when_filename_is_opaque():
    snapshot = {"name": "design_02.png", "product_type": "sweatshirt"}
    profile = {"visual_context": {"embroidery_text": ["GRANDMA EST. 2026"]}}
    assert source_apparel_intent(snapshot, profile).family == "grandma"
    assert "grandmother" in tiktokshop_semantic_queries(
        "sweatshirt", PLACEMENTS, snapshot, profile,
    )[0]
    generic = tiktokshop_semantic_queries(
        "sweatshirt", PLACEMENTS,
        {"name": "generic-embroidered-sweatshirt.png"}, None,
    )
    assert generic and "person wearing sweatshirt" in generic[0]


def test_query_rotation_preserves_audience_and_placement():
    snapshot = {"name": "godmother sweatshirt", "product_type": "sweatshirt"}
    first = tiktokshop_semantic_queries(
        "sweatshirt", PLACEMENTS, snapshot, {}, scan_attempt_count=0,
    )
    next_round = tiktokshop_semantic_queries(
        "sweatshirt", PLACEMENTS, snapshot, {}, scan_attempt_count=1,
    )
    assert first[0] == next_round[0]
    assert first[1:] != next_round[1:]
    assert all("godmother" in query or "adult woman" in query for query in next_round)


def test_human_votes_reorder_queries_only_within_the_given_family():
    queries = ["grandma query A", "grandma query B", "grandma query C"]
    feedback = [
        ("ref_bad", {"scout_query": "grandma query A", "reference_feedback_kind": "bad"}),
        ("ref_bad", {"scout_query": "grandma query A", "reference_feedback_kind": "ai"}),
        ("ref_good", {"scout_query": "grandma query B", "reference_feedback_kind": "good"}),
        ("ref_good", {"scout_query": "grandma query B", "reference_feedback_kind": "good"}),
        # An unreviewed approved import is not an explicit human vote.
        ("ref_good", {"scout_query": "grandma query C"}),
    ]
    ranked, scores = rank_tiktokshop_queries_from_feedback(queries, feedback)
    assert ranked[0] == "grandma query B"
    assert ranked[-1] == "grandma query A"
    assert scores["grandma query b"] > scores["grandma query a"]
    unchanged, _ = rank_tiktokshop_queries_from_feedback(
        queries, [("ref_bad", {"scout_query": "grandma query A", "reference_feedback_kind": "bad"})],
    )
    assert unchanged == queries  # minimum two consistent human votes


def _campaign(session: Session, name: str, root: str, tenant: str = "tenant-a"):
    campaign = RrugcCampaignModel(
        id=tenant + "-" + name, tenant_id=tenant,
        name=name, query=name, target_count=50,
        scout_token_hash="a" * 64, created_by_user_id="system",
    )
    plan = RrugcSourcePlanModel(
        id=tenant + "-plan-" + name, tenant_id=tenant, root_folder_id=root,
        source_file_id=tenant + "-" + name,
        source_relative_path=name + ".jpg", source_name=name + ".jpg",
        source_mime_type="image/jpeg", source_revision="hash-" + name,
        campaign_id=campaign.id, created_by_user_id="system", status="ready",
    )
    session.add_all([campaign, plan])
    session.flush()
    return campaign


def test_reserves_pin_across_tiktok_products_but_not_gatorhats_or_tenants():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        grandma = _campaign(session, "grandma", TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID)
        godmother = _campaign(session, "godmother", TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID)
        gator = _campaign(session, "hat", RRUGC_SOURCE_ROOT_FOLDER_ID)
        other_tenant = _campaign(session, "grandma", TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID, "tenant-b")
        item = CandidateSubmission(
            pin_url="https://www.pinterest.com/pin/4523412345/",
            image_url="https://i.pinimg.com/736x/a/b/c.jpg",
            alt_text="woman wearing embroidered sweatshirt",
        )
        service = RrugcService(session)
        first, created, existing = service.ingest_candidates(
            campaign=grandma, submissions=[item], source_query="grandma lifestyle"
        )
        assert (created, existing) == (1, 0)
        assert first[0].status == "analysis_queued"

        duplicate, created, existing = service.ingest_candidates(
            campaign=godmother, submissions=[item], source_query="godmother lifestyle"
        )
        assert (created, existing) == (0, 1)
        assert duplicate[0].status == "rejected_duplicate"
        assert duplicate[0].reject_reason == "EXACT_SOURCE_DUPLICATE"
        assert duplicate[0].ai_signal_json["duplicate_exact_campaign_id"] == grandma.id

        # Same campaign is idempotent; separate source roots/tenants stay isolated.
        same, created, existing = service.ingest_candidates(
            campaign=grandma, submissions=[item]
        )
        assert (created, existing) == (0, 1) and same[0].id == first[0].id
        for campaign in (gator, other_tenant):
            _, created, existing = service.ingest_candidates(
                campaign=campaign, submissions=[item]
            )
            assert (created, existing) == (1, 0)
    engine.dispose()


def test_visual_dedupe_scopes_to_tiktok_root_and_respects_human_reject():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        grandmother = _campaign(session, "grandmother", TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID)
        godmother = _campaign(session, "godmother", TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID)
        hat = _campaign(session, "hat", RRUGC_SOURCE_ROOT_FOLDER_ID)
        service = RrugcService(session)
        saved = []
        for campaign, suffix in ((grandmother, "a"), (godmother, "b"), (hat, "c")):
            rows, _, _ = service.ingest_candidates(
                campaign=campaign, submissions=[CandidateSubmission(
                    pin_url="https://www.pinterest.com/pin/different-" + suffix + "/",
                    image_url="https://i.pinimg.com/736x/" + suffix + ".jpg",
                )],
            )
            row = rows[0]
            row.status = "approved"
            row.analyzed_at = datetime.now(timezone.utc)
            session.flush()
            session.add(RrugcVisualFingerprintModel(
                tenant_id=campaign.tenant_id, campaign_id=campaign.id,
                candidate_id=row.id, fingerprint="fingerprint-" + suffix,
            ))
            saved.append(row)
        session.commit()
        repo = RrugcRepository(session)
        assert set(repo.visual_fingerprint_rows(
            "tenant-a", exclude_id=saved[1].id, workspace="tiktokshop"
        )) == {"fingerprint-a"}
        assert "fingerprint-c" not in repo.visual_fingerprint_rows(
            "tenant-a", exclude_id=saved[1].id, workspace="tiktokshop"
        )
        saved[0].ai_signal_json = {"reference_manual_label": "bad"}
        session.commit()
        assert repo.visual_fingerprint_rows(
            "tenant-a", exclude_id=saved[1].id, workspace="tiktokshop"
        ) == []
    engine.dispose()
