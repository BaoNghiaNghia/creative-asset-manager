"""Slot 6 may reuse verified same-tenant Scout refs without re-AI or data writes."""
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.modules.realistic_review_ugc.model import RrugcCampaignModel, RrugcCandidateModel
from app.modules.realistic_review_ugc.service import RrugcService
from app.modules.realistic_review_ugc.ugc_scene_selection import (
    eligible_ugc_references, select_ugc_references,
)
from app.modules.realistic_review_ugc.ugc_verified_reuse import load_products_ugc_references


def _campaign(service, name, tenant):
    row, _token = service.create_campaign(
        tenant_id=tenant, user_id="operator",
        name=name, query="authentic cap lifestyle",
        target_count=20, max_scroll_batches=2,
        auto_import=False,
    )
    return row


def test_per_design_borrows_only_verified_approved_originals_without_data_mutation():
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    RrugcCampaignModel.__table__.create(engine)
    RrugcCandidateModel.__table__.create(engine)
    with Session(engine) as session:
        service = RrugcService(session)
        source = _campaign(service, "Original", "tenant-a")
        target = _campaign(service, "Design-specific", "tenant-a")
        foreign = _campaign(service, "Different tenant", "tenant-b")
        own_ids = []
        for index in range(6):
            original = RrugcCandidateModel(
                id=f"original-{index}", tenant_id="tenant-a",
                campaign_id=source.id, source_key=f"source-{index}",
                pin_url=f"https://www.pinterest.com/pin/source-{index}/",
                image_url=f"https://i.pinimg.com/736x/verified-{index}.jpg",
                status="drive_ready" if index < 5 else "rejected_context",
                content_hash=f"hash-{index}", head_visible=True,
                people_count=1, mobile_ugc_score=0.86, final_score=.9 - index * .01,
                ai_signal_json={},
            )
            session.add(original)
            duplicate = RrugcCandidateModel(
                id=f"duplicate-{index}", tenant_id="tenant-a",
                campaign_id=target.id, source_key=f"duplicate-{index}",
                pin_url=original.pin_url,
                image_url=f"https://i.pinimg.com/236x/duplicate-{index}.jpg",
                status="rejected_duplicate", reject_reason="EXACT_SOURCE_DUPLICATE",
                ai_signal_json={
                    "duplicate_exact_candidate_id": original.id,
                    **({"reference_manual_label": "bad"} if index == 4 else {}),
                },
            )
            own_ids.append(duplicate.id)
            session.add(duplicate)
        foreign_candidate = RrugcCandidateModel(
            id="foreign-verified", tenant_id="tenant-b",
            campaign_id=foreign.id, source_key="foreign-original",
            pin_url="https://www.pinterest.com/pin/foreign/",
            image_url="https://i.pinimg.com/736x/foreign.jpg",
            status="drive_ready", head_visible=True, people_count=1,
            mobile_ugc_score=.9,
        )
        session.add(foreign_candidate)
        session.add(RrugcCandidateModel(
            id="cross-tenant-duplicate", tenant_id="tenant-a",
            campaign_id=target.id, source_key="cross-tenant-source",
            pin_url=foreign_candidate.pin_url, image_url=foreign_candidate.image_url,
            status="rejected_duplicate", reject_reason="EXACT_SOURCE_DUPLICATE",
            ai_signal_json={"duplicate_exact_candidate_id": "foreign-verified"},
        ))
        session.commit()

        candidates = load_products_ugc_references(
            session, tenant_id="tenant-a", campaign_id=target.id,
        )
        visible = eligible_ugc_references(candidates)
        assert {candidate.id for candidate in visible} == set(own_ids[:4])
        assert all(candidate.reused_verified_source for candidate in visible)
        assert all(candidate.image_url.endswith(
            f"verified-{index}.jpg") for index, candidate in enumerate(visible))
        assert len(select_ugc_references(
            candidates, selection_key="tenant-a:design:revision:charcoal",
        )) == 3
        assert [item.id for item in select_ugc_references(
            candidates, selection_key="tenant-a:design:revision:charcoal",
        )] == [item.id for item in select_ugc_references(
            list(reversed(candidates)),
            selection_key="tenant-a:design:revision:charcoal",
        )]

        # Reuse is a read-only view; AI verdicts and import status are intact.
        persisted = list(session.scalars(
            select(RrugcCandidateModel).where(
                RrugcCandidateModel.campaign_id == target.id,
            )
        ))
        assert all(item.status == "rejected_duplicate" for item in persisted)
        assert not any(item.id == "cross-tenant-duplicate" for item in visible)
    engine.dispose()
