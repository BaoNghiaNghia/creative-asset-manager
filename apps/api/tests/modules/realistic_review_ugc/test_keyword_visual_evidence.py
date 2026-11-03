from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import RrugcReferenceAssetModel as Asset
from app.modules.realistic_review_ugc.typography_model import (
    RrugcTypographyReferenceModel as Reference,
    RrugcTypographyStyleDnaModel as Dna,
)
from app.modules.realistic_review_ugc.keyword_visual_evidence import keyword_visual_evidence


def add_dna(session, *, tenant="tenant-a", keyword_id="keyword-a",
            rights="owned", editorial="approved", status="ready", fingerprint="tdh1:0f0f0f0f0f0f0f0f"):
    content = uuid4().hex + uuid4().hex
    aid, rid = str(uuid4()), str(uuid4())
    session.add(Asset(
        id=aid, tenant_id=tenant, source_type="manual", source_key=str(uuid4()),
        content_hash=content, remote_file_id="ref-" + aid,
        created_by_user_id="user-a", reference_type="typography",
    ))
    session.flush()
    session.add(Reference(
        id=rid, tenant_id=tenant, workspace_id="workspace",
        asset_id=aid, content_hash=content, scope="keyword",
        keyword_id=keyword_id, source_type="upload",
        source_rights_status=rights, editorial_status=editorial,
        analysis_status=status, created_by_user_id="user-a",
    ))
    session.flush()
    session.add(Dna(
        id=str(uuid4()), tenant_id=tenant, reference_id=rid,
        schema_version="typography-dna/v1", analyzer_version="g3-test",
        image_hash=content,
        typography_region_json={"x": 0.1, "y": 0.2, "w": 0.7, "h": 0.5},
        style_family="Western",
        font_mood_json={"primary": "serif"},
        layout_graph_json={"nodes": [{"id": "a"}], "edges": []},
        overlap_rules_json=[], perceptual_hash=fingerprint,
        confidence=.9,
    ))
    session.flush()
    return rid


def test_g3_evidence_is_keyword_tenant_and_rights_scoped():
    engine = create_engine("sqlite://")
    for model in (Asset, Reference, Dna):
        model.__table__.create(engine)
    with Session(engine) as session:
        left = add_dna(session)
        right = add_dna(session, fingerprint="tdh1:0f0f0f0f0f0f0f0e")
        add_dna(session, rights="blocked")
        add_dna(session, editorial="candidate")
        add_dna(session, status="pending")
        add_dna(session, tenant="tenant-b")
        add_dna(session, keyword_id="keyword-b")
        evidence = keyword_visual_evidence(session, tenant_id="tenant-a", keyword_id="keyword-a", user_id="user-a")
        assert evidence["status"] == "approved_g3_references"
        assert evidence["analyzed_count"] == 2
        assert {item["reference_id"] for item in evidence["items"]} == {left, right}
        assert evidence["similar_pairs"][0]["distance"] == 1
        assert not evidence["candidate_image_checked"]
        assert not evidence["originality_verified"]
        assert keyword_visual_evidence(session, tenant_id="tenant-c", keyword_id="keyword-a")["analyzed_count"] == 0
        assert keyword_visual_evidence(session, tenant_id="tenant-a", keyword_id="keyword-none")["analyzed_count"] == 0
        # Private G3 references must not leak across users in one tenant.
        assert keyword_visual_evidence(session, tenant_id="tenant-a", keyword_id="keyword-a",
                                      user_id="another-user")["analyzed_count"] == 0
        own = session.get(Reference, left)
        own.privacy = "workspace"
        assert keyword_visual_evidence(session, tenant_id="tenant-a", keyword_id="keyword-a",
                                      user_id="another-user")["analyzed_count"] == 1
    engine.dispose()


def test_g3_hash_with_stale_manual_roi_does_not_qualify_for_similarity():
    engine = create_engine("sqlite://")
    for model in (Asset, Reference, Dna):
        model.__table__.create(engine)
    with Session(engine) as session:
        one = add_dna(session)
        two = add_dna(session)
        dna = session.query(Dna).filter(Dna.reference_id == one).one()
        dna.manual_override_json = {"override": {"typography_region": {"x": .3}}}
        evidence = keyword_visual_evidence(session, tenant_id="tenant-a", keyword_id="keyword-a", user_id="user-a")
        assert evidence["analyzed_count"] == 2
        assert evidence["similar_pairs"] == []
        assert not next(item for item in evidence["items"] if item["reference_id"] == one)["perceptual_hash_available"]
    engine.dispose()
