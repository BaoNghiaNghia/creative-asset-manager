"""Prevent database stage constraints from rejecting UGC/Multi-hat jobs."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def test_migration_allows_all_scene_and_ecommerce_stages():
    migration = Path(__file__).resolve().parents[5] / "database/migrations/versions/0183_allow_gatorhats_scene_stages.py"
    spec = spec_from_file_location("migration_scene_stages", migration)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    sql = module._check(module.OLD_STAGES + module.SCENE_STAGES)
    for stage in ("prod_s2_ec1", "prod_s2_ec4", "prod_s2_mh_1", "prod_s2_mh_2",
                  "prod_s2_mh_3", "prod_s2_ugc_1", "prod_s2_ugc_2", "prod_s2_ugc_3"):
        assert f"'{stage}'" in sql
    assert len(module.OLD_STAGES + module.SCENE_STAGES) == 10
