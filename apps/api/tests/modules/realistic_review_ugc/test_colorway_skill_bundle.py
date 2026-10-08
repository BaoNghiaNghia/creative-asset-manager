"""The Stage 2 color scaling Skill must remain separate from 8869 Image Studio."""
from pathlib import Path
from shutil import copytree
from types import SimpleNamespace

from app.modules.realistic_review_ugc.colorways import DEFAULT_COLORWAY_SKILL, STOCK_SKILL
from app.modules.realistic_review_ugc.stage2_skills import (
    list_stage2_skill_catalog, resolve_stage2_skill, verify_stage2_skill_runtime,
)
from app.providers.ai.codex_image import load_codex_skill_manifest


def test_bundled_scale_skill_works_with_stage2_catalog(tmp_path):
    project = Path(__file__).resolve().parents[5]
    source = project / "deploy" / "codex" / "skills" / DEFAULT_COLORWAY_SKILL
    assert (source / "SKILL.md").is_file()
    assert (source / "manifest.json").is_file()
    assert DEFAULT_COLORWAY_SKILL != STOCK_SKILL
    target = tmp_path / "skills" / DEFAULT_COLORWAY_SKILL
    copytree(source, target)
    settings = SimpleNamespace(
        CODEX_IMAGE_HOME=str(tmp_path),
        OPENAI_API_KEY=None,
        OPENAI_BASE_URL=None, OPENAI_PROJECT=None, OPENAI_ORGANIZATION=None,
    )
    manifest = load_codex_skill_manifest(tmp_path, DEFAULT_COLORWAY_SKILL)
    assert manifest is not None
    assert manifest.required_reference_roles == ("design_reference",)
    assert "image_studio" in manifest.workflows
    assert manifest.max_references == 1
    catalog = list_stage2_skill_catalog(settings, refresh=True)
    chosen = next(item for item in catalog.items if item.skill_name == DEFAULT_COLORWAY_SKILL)
    assert chosen.ready is True
    resolved = resolve_stage2_skill(settings=settings, skill_source=None, skill_id=None, skill_name=None, skill_version=None, fallback_skill_name=DEFAULT_COLORWAY_SKILL)
    assert resolved.skill_name == DEFAULT_COLORWAY_SKILL
    pinned = verify_stage2_skill_runtime(
        settings=settings, skill_source=resolved.source,
        skill_id=resolved.skill_id, skill_name=resolved.skill_name,
        skill_version=resolved.skill_version,
    )
    assert resolved.skill_version == "1.0.0"
    assert pinned.skill_name == DEFAULT_COLORWAY_SKILL
