from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace
import pytest
from app.modules.etsy_spy.design_inventory_v3 import validate_design_inventory_v3
from app.modules.etsy_spy.design_artifacts import project_design_assets

SOURCE = "a"*64
BBOX = [.1,.2,.4,.7]
EVIDENCE = sha256(json.dumps(["cam-design-evidence-v3", SOURCE, "front_center",
    [1000,2000,4000,7000]], separators=(",",":")).encode()).hexdigest()
META = {"inventory_version":3,"listing_type":"couple_set","design_evidence_id":EVIDENCE,
    "artwork_id":"D01","ink_variant_id":"I01","garment_group_id":"G01","set_id":"S01",
    "view_id":"V01","view_side":"front","region":"front_center","crop_sha256":"b"*64,
    "bbox_normalized":BBOX,"crop_bbox_normalized":[.08,.17,.42,.73],
    "observations":[{"observation_id":"O01","view_id":"V01","garment_group_id":"G01",
         "set_id":"S01","view_side":"front","bbox_normalized":BBOX}]}

def test_valid_metadata_projects_couple_identity_without_new_db():
    assert validate_design_inventory_v3(META, SOURCE, "front")==META
    job=SimpleNamespace(id="job",entity_id="1234567890",payload_json={"design_assets":[
        {"id":"c"*24,"position":"front","variant":1,"method":"imagegen","output_sha256":"d"*64,
         "source_sha256":SOURCE,"inventory_v3":META}]})
    projected=project_design_assets(job)[0]["inventory_v3"]
    assert projected["garment_group_id"]=="G01"
    assert projected["set_id"]=="S01"
    assert projected["listing_type"]=="couple_set"
    assert projected["observation_count"]==1
    assert "observations" not in projected

@pytest.mark.parametrize("mutate",[
    lambda x:x.update(design_evidence_id="f"*64),
    lambda x:x.update(crop_bbox_normalized=[.15,.2,.4,.7]),
    lambda x:x.update(view_side="back"),
    lambda x:x.update(observations=[]),
    lambda x:x.update(garment_group_id="G02"),
    lambda x:x.update(unknown="not allowed"),
    lambda x:x.update(artwork_id=None),
])
def test_invalid_v3_metadata_fails_closed(mutate):
    value=deepcopy(META);mutate(value)
    with pytest.raises(ValueError):validate_design_inventory_v3(value,SOURCE,"front")

def test_old_assets_still_project_and_source_position_mismatch_rejected():
    assert validate_design_inventory_v3(None,SOURCE,"front") is None
    with pytest.raises(ValueError):validate_design_inventory_v3(META,"e"*64,"front")
    with pytest.raises(ValueError):validate_design_inventory_v3(META,SOURCE,"back")
