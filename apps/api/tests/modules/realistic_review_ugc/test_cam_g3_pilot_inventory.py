"""G3 inventory never grants rights or calls providers; aggregates scoped metadata."""
import importlib.util
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import RrugcReferenceAssetModel as Asset
from app.modules.realistic_review_ugc.typography_model import (
    RrugcTypographyReferenceModel as Ref,
    RrugcTypographyStyleDnaModel as Dna,
    RrugcTypographyAnalysisJobModel as Job,
)

SOURCE = Path(__file__).resolve().parents[5] / "scripts" / "cam-g3-pilot-inventory.py"
spec = importlib.util.spec_from_file_location("cam_g3_pilot_inventory", SOURCE)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def add(session, *, tenant="a", rights="owned", editorial="approved", dna=False, accept=False):
    ref_id = str(uuid4())
    sha = uuid4().hex + uuid4().hex
    asset = Asset(
        id=str(uuid4()), tenant_id=tenant, source_type="upload",
        source_key=sha, content_hash=sha, profile_key="typography-idea",
        reference_type="other", status="ready", remote_file_id="test-file", created_by_user_id="test-user",
    )
    session.add(asset)
    session.flush()
    ref = Ref(
        id=ref_id, tenant_id=tenant, workspace_id=tenant, asset_id=asset.id,
        content_hash=sha, source_rights_status=rights, editorial_status=editorial,
        created_by_user_id="test-user",
    )
    session.add(ref)
    session.flush()
    if dna:
        review = (
            {"schema_version": "typography-review/v1", "decision": "accepted",
             "image_hash": sha, "analyzer_version": "g3-test", "reason_code": "approved",
             "override": None}
            if accept else None
        )
        session.add(Dna(
            tenant_id=tenant, reference_id=ref_id, image_hash=sha,
            analyzer_version="g3-test", schema_version="typography-dna/v1",
            typography_region_json={"x": .1, "y": .2, "w": .7, "h": .5},
            style_family="Western", font_mood_json={
                "primary": "serif", "secondary": None, "weight": "bold",
                "contrast": "low", "proportions": "wide"},
            layout_graph_json={"nodes": [{"id": "n1", "role": "primary",
                                         "path_type": "straight", "align": "center",
                                         "size_ratio": 1.0}], "edges": []},
            overlap_rules_json=[], confidence=.9, manual_override_json=review,
        ))
    session.flush()
    return ref


def test_redacted_pilot_inventory_does_not_conflate_rights_or_dna_review():
    engine = create_engine("sqlite://")
    for model in (Asset, Ref, Dna, Job):
        model.__table__.create(engine)
    with Session(engine) as session:
        add(session, tenant="a", dna=True, accept=True)
        add(session, tenant="a", dna=True, accept=False)
        add(session, tenant="a", rights="unknown", dna=True, accept=True)
        add(session, tenant="a", editorial="candidate")
        add(session, tenant="b", dna=True, accept=True)
        result = mod.audit(session, tenant_id="a")
        assert result["reference_count"] == 4
        assert result["candidate_rights_and_editorial_count"] == 2
        assert result["rights_unknown_or_blocked_count"] == 1
        assert result["dna_current_count"] == 3
        assert result["dna_human_accepted_count"] == 1
        assert result["eight_reference_metadata_candidate_pool"] is False
        assert result["rights_documents_independently_verified"] is False
        assert result["provider_calls"] == result["writes"] == 0
        assert "test-user" not in str(result)
        assert "tenant" not in str(result)
        global_result = mod.aggregate_inventory(session)
        assert global_result["tenant_count"] == 2
        assert global_result["reference_count"] == 5
        assert global_result["dna_human_accepted_count"] == 2
        assert global_result["tenants_with_eight_metadata_candidates"] == 0
        assert "test-user" not in str(global_result)
        assert global_result["provider_calls"] == global_result["writes"] == 0
    engine.dispose()
