"""Reviews Stage 3 and Stage 4 must share identical, safe embroidery groups."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.embroidery_groups import group_source_plan_rows
from app.modules.realistic_review_ugc.model import RrugcSourcePlanModel
from app.modules.realistic_review_ugc.router import _group_source_plan_rows
from app.modules.realistic_review_ugc.stage2 import _stage2_source_group
from app.modules.realistic_review_ugc.source_plans import RRUGC_SOURCE_ROOT_FOLDER_ID


def plan(identifier: str, *, phrase: str | None = None, graphic: str = "",
         campaign: str | None = None, stored: str | None = None,
         root: str = "review-root", tenant: str = "tenant-a",
         status: str = "ready") -> RrugcSourcePlanModel:
    context = {"embroidery_text": [phrase], "embroidery_identity": graphic} if phrase else None
    return RrugcSourcePlanModel(
        id=identifier, tenant_id=tenant, root_folder_id=root,
        source_file_id="file-" + identifier,
        source_relative_path="Colorway/" + identifier + ".png",
        source_name=identifier + ".png", source_mime_type="image/png",
        source_revision=identifier, status=status,
        embroidery_signature=stored,
        visual_context_json=context,
        campaign_id=campaign,
        created_by_user_id="operator",
    )


def grouped_ids(rows):
    return {frozenset(member.id for member in group)
            for group in group_source_plan_rows(rows)}


def test_same_stitching_across_hat_colors_and_angles_groups_once():
    rows = [
        plan("navy-front", phrase="COOL DADS CLUB", graphic="plain centered wording"),
        plan("red-side", phrase="COOL DADS CLUB", graphic="same wording on red cap"),
        plan("natural-angled", phrase="COOL DADS CLUB", graphic="centered stitched words"),
    ]
    assert grouped_ids(rows) == {frozenset(r.id for r in rows)}
    assert grouped_ids(rows) == grouped_ids(list(reversed(rows)))


def test_campaign_mismatch_cannot_bridge_two_different_embroideries_through_weak_member():
    rows = [
        plan("golf", phrase="BEST GRANDPA BY PAR",
             graphic="golfer with golf club", campaign="old-campaign"),
        plan("unanalyzed", campaign="old-campaign", status="queued"),
        plan("hotdog", phrase="BEST GRANDPA BY PAR",
             graphic="hotdog illustration", campaign="old-campaign"),
    ]
    # The legacy campaign is contaminated: never attach an unanalysed
    # source to either finished design by arbitrary iteration order.
    expect = {frozenset({r.id}) for r in rows}
    assert grouped_ids(rows) == expect
    assert grouped_ids(list(reversed(rows))) == expect


def test_unambiguous_campaign_joins_unanalyzed_row_despite_stale_legacy_hash():
    rows = [
        plan("navy", phrase="BEST GRANDPA BY PAR",
             graphic="golf club", campaign="shared"),
        plan("red-old", campaign="shared", stored="old-signature", status="queued"),
    ]
    assert grouped_ids(rows) == {frozenset({"navy", "red-old"})}


def test_legacy_campaign_with_two_stale_hashes_still_recovers_single_group():
    rows = [
        plan("legacy-a", stored="v1", campaign="shared"),
        plan("legacy-b", stored="v2", campaign="shared"),
    ]
    assert grouped_ids(rows) == {frozenset({"legacy-a", "legacy-b"})}


def test_never_groups_across_root_folder_or_tenant():
    rows = [
        plan("a", phrase="COOL DADS CLUB", campaign="same"),
        plan("b", phrase="COOL DADS CLUB", campaign="same", root="other"),
        plan("c", phrase="COOL DADS CLUB", campaign="same", tenant="tenant-b"),
    ]
    assert grouped_ids(rows) == {frozenset({r.id}) for r in rows}


def test_stage4_history_does_not_pull_colorway_artwork_from_reviews_stage2():
    engine = create_engine("sqlite://")
    RrugcSourcePlanModel.__table__.create(engine)
    with Session(engine) as session:
        source = plan("finished-hat", root=RRUGC_SOURCE_ROOT_FOLDER_ID,
                      phrase="COOL DADS CLUB", graphic="centered words")
        intermediate = plan("intermediate", root=RRUGC_SOURCE_ROOT_FOLDER_ID,
                            phrase="COOL DADS CLUB", graphic="centered words")
        intermediate.source_name = "embroidery_cool_dads_club.png"
        session.add_all([source, intermediate])
        session.commit()
        history = _stage2_source_group(
            session, tenant_id="tenant-a", plan=source)
        assert {item.id for item in history} == {"finished-hat"}
    engine.dispose()


def test_stage4_job_history_uses_exact_stage3_group_members_including_missing():
    engine = create_engine("sqlite://")
    RrugcSourcePlanModel.__table__.create(engine)
    with Session(engine) as session:
        rows = [
            plan("navy", phrase="COOL DADS CLUB", graphic="centered wording",
                 campaign="shared"),
            plan("red", phrase="COOL DADS CLUB", graphic="same words",
                 campaign="shared", status="missing"),
            plan("golf", phrase="COOL DADS CLUB", graphic="golf motif",
                 campaign="shared"),
            plan("bridge", campaign="shared", status="queued"),
        ]
        session.add_all(rows)
        session.commit()
        canonical = _group_source_plan_rows(rows)
        assert {frozenset(row.id for row in group) for group in canonical} == grouped_ids(rows)
        for row in rows:
            from_history = _stage2_source_group(
                session, tenant_id="tenant-a", plan=row)
            matches = [group for group in canonical if row.id in {p.id for p in group}]
            assert len(matches) == 1
            assert {p.id for p in from_history} == {p.id for p in matches[0]}
    engine.dispose()
