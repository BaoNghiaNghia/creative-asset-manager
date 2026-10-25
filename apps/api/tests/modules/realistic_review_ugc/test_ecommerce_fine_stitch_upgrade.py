"""Regression tests for the four versioned, non-destructive Ecommerce Skill upgrades."""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.operations.upgrade_ecommerce_fine_stitch_skills import (
    SLOTS, MIGRATION_TAG, build_updated_zip, flatten_relief,
    generation_instruction, new_name, upgrade_script,
)


def sample_source_zip(original: str) -> bytes:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("SKILL.md", f"---\nname: {original}\ndescription: Original standalone skill\n---\n\n# Legacy instructions\nNever use the imagegen tool in standalone mode.\n")
        z.writestr("manifest.json", json.dumps({
            "schema_version": 1, "skill_name": original,
            "display_name": original, "description": "Original",
            "workflows": ["image_studio"], "required_reference_roles": [],
            "optional_reference_roles": [], "product_types": ["hat"],
            "max_references": 8,
        }))
        z.writestr("assets/cap-original.png", b"\x89PNG\r\n\x1a\noriginal pixel data")
        z.writestr("references/madeira-colorways.tsv", b"natural-charcoal\tshade-01\n")
    return archive.getvalue()


@pytest.mark.parametrize("stage,original", SLOTS)
def test_packs_a_new_name_preserving_original_assets(stage, original):
    raw = sample_source_zip(original)
    result = build_updated_zip(original, stage, raw)
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        old = zipfile.ZipFile(io.BytesIO(raw))
        assert archive.read("assets/cap-original.png") == old.read("assets/cap-original.png")
        assert archive.read("references/madeira-colorways.tsv") == old.read("references/madeira-colorways.tsv")
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["skill_name"] == new_name(original)
        assert manifest["workflows"] == ["image_studio"]
        doc = archive.read("SKILL.md").decode("utf-8")
        assert f"name: {new_name(original)}" in doc
        assert MIGRATION_TAG in archive.read("references/CAM_FINE_THREAD_V1.txt").decode()
        assert "Never use the imagegen tool in standalone mode." in doc
        assert "CAM product image generation" in doc
        assert "output/final.png" in doc
        assert "fine" in doc.lower()
        assert "NEVER produce puffy/foam embroidery" in doc


@pytest.mark.parametrize("stage,original", SLOTS)
def test_each_view_has_explicit_angle_and_no_invented_art(stage, original):
    instructions = generation_instruction(stage)
    assert "Role-labeled references" in instructions
    assert "stock" in instructions
    assert "no" in instructions.lower()
    if stage == "prod_s2_ec1":
        assert "FRONT" in instructions
    elif stage == "prod_s2_ec2":
        assert "THREE-QUARTER" in instructions
    elif stage == "prod_s2_ec3":
        assert "SIDE 90 DEGREES" in instructions
    else:
        assert "REAR 180 DEGREES" in instructions
        assert "unembroidered" in instructions


def test_renderer_suppresses_broad_emboss_relief_not_stitch_detail():
    original = """MATTE = 0.65
lo = np.where(low > 1, 1 + (low - 1) * (1 - matte), 1 + (low - 1) * (1 - 0.35 * matte))
high = 1 + (high - 1) * (1 - 0.3 * matte)
rel2 = np.clip(lo * high, 0, 1 + 0.6 * (1 - matte) + 0.15)
fab = reg * (1 - 0.30 * shadow[..., None])
"""
    updated = upgrade_script("prod_s2_ec1", "scripts/run.py", original.encode()).decode()
    assert "MATTE = 0.86" in updated
    assert "1 - 0.90 * matte" in updated
    assert "high = 1 + (high - 1) * (1 - 0.3 * matte)" in updated
    assert "np.clip(lo * high, 0.82, 1.15)" in updated
    assert "1 - 0.08 * shadow" in updated
    assert "MATTE = 0.65" not in updated
    assert "lo = 1 + (low - 1)" in flatten_relief(original, "run.py")
