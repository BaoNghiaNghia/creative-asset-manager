"""Concept Short v1 adapter contract: don't confuse flat concepts with stitched finals."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.modules.realistic_review_ugc.keyword_images import keyword_prompt
from app.modules.realistic_review_ugc.stage2_skills import (
    _ensure_uploaded_local_manifest, UploadedStage2SkillBundle,
    Stage2SkillRegistryError,
)
from app.providers.ai import codex_image
from app.providers.ai.codex_image import (
    CodexImageGenRunner, CodexImageRunnerConfig, CodexImageProviderError,
    _distinct_imagegen_calls, _concept_short_skipped_slots,
)


def _bundle(*, complete=True):
    files = [
        ("scripts/make_concepts.py", b"pass", "text/x-python"),
        ("scripts/typo_engine.py", b"pass", "text/x-python"),
        ("scripts/workcanvas.py", b"pass", "text/x-python"),
        ("references/concepts.json", b"{}", "application/json"),
        ("references/fonts.json", b"{}", "application/json"),
        ("references/prompt_A_stitch.txt", b"Edit source", "text/plain"),
    ]
    if not complete:
        files.pop()
    return UploadedStage2SkillBundle(
        skill_name="concept-short-v1", display_name="Concept Short v1",
        description="Six typography plans plus independent image edits",
        version="v1", bundle_sha256="abc", files=tuple(files),
    )


def test_only_complete_concept_short_upload_gets_stage1_manifest(tmp_path):
    _ensure_uploaded_local_manifest(tmp_path, _bundle())
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["skill_name"] == "concept-short-v1"
    assert "keyword_six_designs" in manifest["workflows"]
    assert "image_studio" in manifest["workflows"]


def test_incomplete_concept_short_upload_fails_closed(tmp_path):
    with pytest.raises(Stage2SkillRegistryError, match="original scripts"):
        _ensure_uploaded_local_manifest(tmp_path, _bundle(complete=False))


def test_unrelated_upload_does_not_get_stage1_compatibility(tmp_path):
    bundle = _bundle()
    other = UploadedStage2SkillBundle(
        skill_name="some-other-skill", display_name="Other",
        description="", version=None, bundle_sha256="abc", files=bundle.files,
    )
    _ensure_uploaded_local_manifest(tmp_path, other)
    assert json.loads((tmp_path / "manifest.json").read_text())["workflows"] == ["image_studio"]


def test_concept_short_stage1_instructions_keep_code_and_image_edit_separate():
    prompt = keyword_prompt("Y'ALL NEED JESUS", skill_name="concept-short-v1")
    runner = CodexImageGenRunner(CodexImageRunnerConfig(
        skill_name="concept-short-v1", output_contract="stage1_six_final_designs",
    ))
    contract = runner._prompt(person_name=None, references=[], user_prompt=prompt)
    assert "make_concepts.py" in contract
    assert "image-EDIT" in contract
    assert "A_prompt.txt" in contract
    assert "C4->C7" in contract and "C5->C8" in contract
    assert "skipped/fail_size" in contract
    assert "flat raster may be called a stitched final" in contract
    assert "No code-generated flat concept" not in contract
    assert "Never call Codex, imagegen" not in prompt


def test_imagegen_call_counter_requires_genuine_tool_events():
    rows = [
        {"type": "item.started", "item": {"id": "one", "type": "tool_call", "tool_name": "imagegen"}},
        {"type": "item.completed", "item": {"id": "one", "type": "tool_call", "tool_name": "imagegen"}},
        {"type": "item.completed", "item": {"id": "two", "type": "image_generation"}},
        {"type": "item.completed", "item": {"id": "not-image", "type": "tool_call", "name": "functions.exec"}},
    ]
    assert _distinct_imagegen_calls("\n".join(map(json.dumps, rows))) == 2
    assert _distinct_imagegen_calls('{"type":"turn.completed"}') == 0


def test_flat_concepts_cannot_be_mislabeled_finished_images(tmp_path, monkeypatch):
    skill = tmp_path / "codex_home" / "skills" / "concept-short-v1"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("name: concept-short-v1")
    root = tmp_path / "staging"
    attempt = "job-123"

    async def fake_build(*, script, quote, workspace):
        assert quote == "Y'ALL NEED JESUS"
        concepts = workspace / "working" / "concept-short" / "y-all-need-jesus"
        concepts.mkdir(parents=True, exist_ok=True)
        report = []
        for number in range(1, 7):
            concept = f"C{number}"
            directory = concepts / concept
            directory.mkdir()
            Image.new("RGB", (1536, 1024), "white").save(directory / "A_input.png")
            (directory / "A_prompt.txt").write_text("Edit the attached reference only.")
            (directory / "spec.json").write_text("{}")
            report.append({"concept": concept, "status": "ok"})
        (concepts / "summary.json").write_text(json.dumps(report))

    async def fake_exec(*_argv, **_kwargs):
        folder = root / "codex" / attempt / "output" / "final"
        folder.mkdir(parents=True, exist_ok=True)
        for number in range(1, 7):
            Image.new("RGB", (1536, 1024), "white").save(
                folder / f"design_{number:02}.png"
            )
        return SimpleNamespace(returncode=0)

    async def fake_stream(*_args, **_kwargs):
        return '{"type":"turn.completed"}', ""

    monkeypatch.setattr(codex_image.shutil, "which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr(codex_image.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(codex_image, "stream_codex_process", fake_stream)
    monkeypatch.setattr(codex_image, "_build_concept_short_source", fake_build)
    runner = CodexImageGenRunner(CodexImageRunnerConfig(
        skill_name="concept-short-v1", output_contract="stage1_six_final_designs",
        codex_home=str(tmp_path / "codex_home"), staging_root=str(root),
        expected_quote="Y'ALL NEED JESUS",
    ))

    async def generate():
        return await runner.generate_from_references(
            attempt_id=attempt, person=None, references=[],
            prompt="Y'ALL NEED JESUS",
        )
    with pytest.raises(CodexImageProviderError) as exc:
        asyncio.run(generate())
    assert exc.value.code == "concept_short_image_edits_missing"


def test_incomplete_uploaded_zip_with_manifest_is_rejected(tmp_path):
    from io import BytesIO
    import zipfile
    from app.modules.realistic_review_ugc.stage2_skills import inspect_uploaded_stage2_skill_bundle
    with BytesIO() as memory:
        with zipfile.ZipFile(memory, "w") as archive:
            archive.writestr(
                "SKILL.md",
                "---\nname: concept-short-v1\ndescription: Test incomplete.\n---\n",
            )
            archive.writestr(
                "manifest.json",
                json.dumps({
                    "schema_version": 1, "skill_name": "concept-short-v1",
                    "display_name": "Concept Short",
                    "workflows": ["image_studio", "keyword_six_designs"],
                }),
            )
        content = memory.getvalue()
    with pytest.raises(Stage2SkillRegistryError, match="original scripts"):
        inspect_uploaded_stage2_skill_bundle(content)


def test_image_gen_underscore_telemetry_is_recognized():
    event = {"type": "item.completed", "item": {
        "type": "mcp_tool_call", "tool_name": "image_gen", "id": "call_123"
    }}
    assert _distinct_imagegen_calls(json.dumps(event)) == 1


def test_missing_source_plan_is_not_accepted_as_embroidery(tmp_path):
    from app.providers.ai.codex_image import _assert_concept_short_plan
    with pytest.raises(CodexImageProviderError) as err:
        _assert_concept_short_plan(tmp_path)
    assert err.value.code == "concept_short_source_missing"


def test_concept_short_has_time_for_six_independent_image_edits():
    from app.modules.realistic_review_ugc.keyword_images import codex_stage1_timeout_seconds
    assert codex_stage1_timeout_seconds("concept-short-v1", 900) == 1800
    assert codex_stage1_timeout_seconds("concept-short-v1", 2400) == 2400
    assert codex_stage1_timeout_seconds("gatorhats-typography-concepts", 900) == 900


def test_source_renderer_receives_literal_quote_without_shell(tmp_path):
    from app.providers.ai.codex_image import _build_concept_short_source
    script_dir = tmp_path / "installed" / "scripts"
    script_dir.mkdir(parents=True)
    script = script_dir / "make_concepts.py"
    script.write_text(
        "import json, pathlib, sys\n"
        "p = pathlib.Path(sys.argv[3]); p.mkdir(parents=True, exist_ok=True)\n"
        "(p / 'received.json').write_text(json.dumps(sys.argv[1:]))\n"
    )
    workspace = tmp_path / "job"
    workspace.mkdir()
    asyncio.run(_build_concept_short_source(
        script=script, quote="Y'ALL NEED JESUS", workspace=workspace,
    ))
    args = json.loads(
        (workspace / "working" / "concept-short" / "received.json").read_text()
    )
    assert args[0] == "Y'ALL NEED JESUS"
    assert args[1] == "--out"
