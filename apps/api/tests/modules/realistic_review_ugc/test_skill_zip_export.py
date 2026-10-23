from __future__ import annotations

import io
import zipfile

import pytest

from app.modules.realistic_review_ugc import stage2_skills


def test_export_local_skill_is_reimportable_and_excludes_metadata(tmp_path):
    root = tmp_path / "skills" / "test-hat-skill"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: test-hat-skill\ndescription: Test embroidery artwork\n---\n",
        encoding="utf-8",
    )
    (root / "manifest.json").write_text('{"workflows":["image_studio"]}', encoding="utf-8")
    (root / "assets").mkdir()
    (root / "assets" / "pattern.txt").write_text("thread pattern", encoding="utf-8")
    (root / ".env").write_text("secret", encoding="utf-8")
    (root / ".openai-skill.json").write_text('{"internal":true}', encoding="utf-8")
    class Settings:
        CODEX_IMAGE_HOME = str(tmp_path)
    content = stage2_skills.export_installed_stage2_skill_bundle(
        "test-hat-skill", settings=Settings(),
    )
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = archive.namelist()
    assert "SKILL.md" in names
    assert "assets/pattern.txt" in names
    assert ".env" not in names
    assert ".openai-skill.json" not in names
    parsed = stage2_skills.inspect_uploaded_stage2_skill_bundle(content)
    assert parsed.skill_name == "test-hat-skill"


def test_export_rejects_symlink_and_missing_skills(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "example").symlink_to(tmp_path, target_is_directory=True)
    class Settings:
        CODEX_IMAGE_HOME = str(tmp_path)
    with pytest.raises(stage2_skills.Stage2SkillRegistryError) as exc:
        stage2_skills.export_installed_stage2_skill_bundle(
            "example", settings=Settings(),
        )
    assert exc.value.status_code == 422
    with pytest.raises(stage2_skills.Stage2SkillRegistryError) as missing:
        stage2_skills.export_installed_stage2_skill_bundle(
            "not-installed", settings=Settings(),
        )
    assert missing.value.status_code == 404
