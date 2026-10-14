"""Validate Stage 1's six-file output contract before any Drive upload."""
from pathlib import Path

import pytest
from PIL import Image

from app.providers.ai.codex_image import CodexImageGenRunner, CodexImageRunnerConfig, CodexImageProviderError


def runner():
    return CodexImageGenRunner(CodexImageRunnerConfig(
        skill_name="gatorhats-stage1-six-designs",
        output_contract="stage1_six_final_designs",
    ))


def outputs(tmp_path: Path):
    final = tmp_path / "output" / "final"
    final.mkdir(parents=True)
    for number in range(1, 7):
        Image.new("RGBA", (800, 800), (number * 32, 80, 180, 255)).save(final / f"design_{number:02}.png")
    return final



def test_stage1_checkpoint_inspection_handles_missing_and_corrupt_files(tmp_path):
    final = outputs(tmp_path)
    assert runner().stage1_missing_final_numbers(tmp_path) == ()
    for number in (3, 4, 5, 6):
        (final / f"design_{number:02}.png").unlink()
    assert runner().stage1_missing_final_numbers(tmp_path) == (3, 4, 5, 6)
    (final / "design_02.png").write_bytes((final / "design_01.png").read_bytes())
    assert runner().stage1_missing_final_numbers(tmp_path) is None


@pytest.mark.parametrize("modify_original", [False, True])
def test_stage1_continuation_only_generates_missing_artwork(tmp_path, monkeypatch, modify_original):
    import asyncio
    from types import SimpleNamespace
    from app.providers.ai import codex_image
    root = tmp_path / "staging"
    workspace = root / "codex" / "job-123"
    final = outputs(workspace)
    for number in (3, 4, 5, 6):
        (final / f"design_{number:02}.png").unlink()
    original = (final / "design_01.png").read_bytes()
    skill_folder = tmp_path / "codex-home" / "skills" / "gatorhats-stage1-six-designs"
    skill_folder.mkdir(parents=True)
    (skill_folder / "SKILL.md").write_text("Stage1 independent embroidery")
    seen = []

    async def fake_exec(*argv, **_kwargs):
        instruction = argv[-1]
        seen.append(instruction)
        assert "ONLY the 4 missing designs" in instruction
        for number in (3, 4, 5, 6):
            Image.new("RGBA", (800, 800), (number * 32, 90, 40, 255)).save(
                final / f"design_{number:02}.png"
            )
        if modify_original:
            Image.new("RGBA", (800, 800), (255, 33, 77, 255)).save(final / "design_01.png")
        return SimpleNamespace(returncode=0)

    async def fake_stream(_process, **_kwargs):
        return '{"type":"turn.completed"}', ""

    monkeypatch.setattr(codex_image.shutil, "which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr(codex_image.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(codex_image, "stream_codex_process", fake_stream)
    test_runner = CodexImageGenRunner(CodexImageRunnerConfig(
        skill_name="gatorhats-stage1-six-designs", output_contract="stage1_six_final_designs",
        codex_home=str(tmp_path / "codex-home"), staging_root=str(root),
    ))

    async def work():
        return await test_runner.generate_from_references(
            attempt_id="job-123", person=None, references=[],
            prompt="BORN BROKE DIE RICH", resume_missing_numbers=(3, 4, 5, 6),
        )

    if modify_original:
        with pytest.raises(CodexImageProviderError, match="checkpoint was restored"):
            asyncio.run(work())
    else:
        result = asyncio.run(work())
        assert len(result.output_files) == 6
        assert all(Path(item.path).is_file() for item in result.output_files)
    assert (final / "design_01.png").read_bytes() == original
    assert len(seen) == 1
    assert "Do not repeat all six" in seen[0]


def test_stage1_preview_cleanup_preserves_final_png_checkpoints(tmp_path):
    final = outputs(tmp_path / "codex" / "stage1-job")
    original = (final / "design_01.png").read_bytes()
    preview = tmp_path / "codex" / "stage1-job" / "working" / "previews"
    preview.mkdir(parents=True)
    (preview / "qa.png").write_bytes(b"discarded preview")
    other = tmp_path / "codex" / "stage1-job" / "working" / "notes.txt"
    other.write_text("important log", encoding="utf-8")
    custom = CodexImageGenRunner(CodexImageRunnerConfig(staging_root=str(tmp_path)))
    custom.cleanup_previews("stage1-job")
    assert not preview.exists()
    assert (final / "design_01.png").read_bytes() == original
    assert other.read_text(encoding="utf-8") == "important log"


def test_stage1_preview_cleanup_does_not_follow_external_symlink(tmp_path):
    folder = tmp_path / "codex" / "stage1-job" / "working"
    folder.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.png").write_bytes(b"important")
    (folder / "previews").symlink_to(outside, target_is_directory=True)
    custom = CodexImageGenRunner(CodexImageRunnerConfig(staging_root=str(tmp_path)))
    custom.cleanup_previews("stage1-job")
    assert not (folder / "previews").exists()
    assert (outside / "keep.png").read_bytes() == b"important"
    custom.cleanup_previews("../outside")
    assert (outside / "keep.png").read_bytes() == b"important"


def test_six_individual_pngs_selected_while_intermediates_ignored(tmp_path):
    final = outputs(tmp_path)
    drafts = tmp_path / "working" / "previews"
    drafts.mkdir(parents=True)
    for number in range(80):
        Image.new("RGB", (80, 80), (30, number * 3, 120)).save(drafts / f"draft_{number:03}.webp")
    found = runner()._collect_six_final_designs(tmp_path)
    assert len(found) == 6
    assert [item.filename for item in found] == [
        f"output/final/design_{number:02}.png" for number in range(1, 7)
    ]
    assert all(Path(item.path).parent == final for item in found)


@pytest.mark.parametrize("problem", ["missing", "extra", "duplicate", "small", "wrong_format"])
def test_six_final_contract_fails_closed(tmp_path, problem):
    final = outputs(tmp_path)
    if problem == "missing":
        (final / "design_06.png").unlink()
    elif problem == "extra":
        Image.new("RGB", (800, 800)).save(final / "design_07.png")
    elif problem == "duplicate":
        (final / "design_06.png").write_bytes((final / "design_05.png").read_bytes())
    elif problem == "small":
        Image.new("RGBA", (128, 128)).save(final / "design_06.png")
    elif problem == "wrong_format":
        Image.new("RGB", (800, 800)).save(final / "design_06.png", format="JPEG")
    with pytest.raises(CodexImageProviderError):
        runner()._collect_six_final_designs(tmp_path)



def test_six_numbered_pngs_in_output_root_are_normalized_to_canonical_final_paths(tmp_path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    for number in range(1, 7):
        Image.new("RGBA", (800, 800), (number * 30, 30, 100)).save(
            output_dir / f"concept-{number}.png"
        )
    saved = runner()._collect_six_final_designs(tmp_path)
    assert [Path(image.path).name for image in saved] == [
        f"design_{n:02}.png" for n in range(1, 7)
    ]
    assert all((output_dir / "final" / f"design_{n:02}.png").is_file() for n in range(1, 7))


def test_six_exact_finals_ignore_harmless_metadata_in_final_dir(tmp_path):
    folder = outputs(tmp_path)
    (folder / "audit.json").write_text('{"status":"ok"}')
    (folder / "notes").mkdir()
    found = runner()._collect_six_final_designs(tmp_path)
    assert len(found) == 6


def test_complete_six_final_set_does_not_reject_intermediate_output_png(tmp_path):
    outputs(tmp_path)
    intermediate = tmp_path / "output" / "artworks"
    intermediate.mkdir()
    Image.new("RGB", (900, 900), (90, 90, 90)).save(intermediate / "draft_01.png")
    found = runner()._collect_six_final_designs(tmp_path)
    assert len(found) == 6
    assert all(Path(item.path).parent.name == "final" for item in found)


def test_partial_set_error_reports_count_and_missing_indexes(tmp_path):
    folder = outputs(tmp_path)
    (folder / "design_06.png").unlink()
    with pytest.raises(CodexImageProviderError, match="5 PNG candidates.*missing design numbers 6"):
        runner()._collect_six_final_designs(tmp_path)

def test_stage1_prompt_does_not_request_v4_ten_concepts_or_colorways():
    value = runner()._prompt(person_name=None, references=[], user_prompt="BEACH LIFE")
    assert "exactly six" in value
    assert "design_06.png" in value
    assert "Do not generate, write, upload, or retain any preview/draft files" in value
    assert "Persist only the six numbered final PNGs" in value
    assert "BEACH LIFE" in value
    assert "TEN separate" not in value
    assert "13 ORIGINAL Valucap" not in value

def test_six_final_designs_normalize_only_complete_numbered_generated_folder(tmp_path):
    folder = tmp_path / "generated"
    folder.mkdir()
    for number in range(1, 7):
        Image.new("RGB", (900, 900), (number * 29, 45, 65)).save(folder / f"design_{number}.png")
    files = runner()._collect_six_final_designs(tmp_path)
    assert len(files) == 6
    assert [Path(item.path).name for item in files] == [f"design_{n:02}.png" for n in range(1, 7)]
    assert all((tmp_path / "output" / "final" / f"design_{n:02}.png").is_file() for n in range(1, 7))


def test_zero_png_is_named_no_generation_and_previews_never_count(tmp_path):
    drafts = tmp_path / "working" / "previews"
    drafts.mkdir(parents=True)
    Image.new("RGBA", (900, 900)).save(drafts / "design_01.png")
    with pytest.raises(CodexImageProviderError) as caught:
        runner()._collect_six_final_designs(tmp_path)
    assert caught.value.code == "stage1_no_generated_images"
    assert "without creating any PNG artwork" in str(caught.value)


def test_partial_alt_folder_does_not_promote_drafts_or_count_as_success(tmp_path):
    folder = tmp_path / "results"
    folder.mkdir()
    for number in range(1, 6):
        Image.new("RGB", (800, 800), (number * 15, 33, 90)).save(folder / f"design_{number:02}.png")
    with pytest.raises(CodexImageProviderError) as caught:
        runner()._collect_six_final_designs(tmp_path)
    assert caught.value.code == "stage1_six_outputs_invalid"
    assert "missing design numbers 6" in str(caught.value)

@pytest.mark.parametrize(
    ("stdout", "stderr", "expected"),
    [
        ('{"type":"turn.completed"}\\n{"type":"item.completed","item":{"type":"agent_message","text":"No assets."}}', "", "stage1_imagegen_not_invoked"),
        ('{"type":"turn.failed","error":{"message":"tool execution failed"}}', "", "stage1_codex_turn_failed"),
        ('{"type":"item.started","item":{"type":"mcp_tool_call","tool_name":"imagegen"}}\\n{"type":"turn.completed"}', "", "stage1_imagegen_no_output"),
        ('{"type":"item.completed","item":{"type":"agent_message","text":"imagegen is not available on this runner."}}', "", "stage1_imagegen_unavailable"),
        ('{"type":"turn.completed"}', "rate limit exceeded for imagegen", "stage1_imagegen_limited"),
        ('{"type":"item.completed","item":{"type":"tool_call","name":"terminal"}}', "", "stage1_no_generated_images"),
    ],
)
def test_empty_stage1_output_reports_actionable_cli_cause_without_exposing_text(stdout, stderr, expected):
    from app.providers.ai.codex_image import _classify_stage1_no_images
    error = _classify_stage1_no_images(stdout.replace(chr(92) + "n", chr(10)), stderr)
    assert error.code == expected
    assert "tool execution failed" not in str(error)
    assert "No assets" not in str(error)


def test_stage1_prompt_uses_selected_six_design_skill_instead_of_hardcoded_name():
    custom = CodexImageGenRunner(CodexImageRunnerConfig(
        skill_name="approved-stage1-six-designs",
        output_contract="stage1_six_final_designs",
    ))
    prompt = custom._prompt(person_name=None, references=[], user_prompt="Saying")
    assert "Use $approved-stage1-six-designs and $imagegen." in prompt
    assert "Use $gatorhats-stage1-six-designs" not in prompt

def test_managed_stage1_natural_stitch_skill_bundle_preserves_strict_six_final_contract():
    from app.operations.install_stage1_six_skill import skill_bundle
    bundle = skill_bundle()
    files = {name: content for name, content, _content_type in bundle.files}
    skill = files["SKILL.md"].decode("utf-8")
    manifest = files["manifest.json"].decode("utf-8")
    assert "version: 16_28_14_10_26" in skill
    assert "typography-only" in skill.lower()
    assert "one single solid thread hue" in skill.lower()
    assert "NO illustrations" in skill
    assert "Do NOT add" in skill
    assert "birds, flowers" in skill.lower()
    assert "at least three illustration-led" not in skill.lower()
    assert "2–4-color" not in skill.lower()
    assert "Do not generate a collage" in skill
    assert "embroidered-satin-natural" in skill
    assert "NOT a flat screen print" in skill
    assert "one final per image-generation call" in skill
    assert "NO hat" in skill
    for index in range(1, 7):
        assert f"output/final/design_{index:02}.png" in skill
    assert "keyword_six_designs" in manifest
    assert "brown-thread embroidery designs" in manifest
    assert "200-family approved DaFont font pool" in manifest
    assert "font_registry.json" in files
    from pathlib import Path
    deployed = Path(__file__).resolve().parents[5] / "deploy/codex/skills/gatorhats-stage1-six-designs/SKILL.md"
    assert deployed.read_bytes() == files["SKILL.md"]


def test_stage1_keyword_prompt_requires_creative_distinct_stitches_and_thread_pigment():
    from app.modules.realistic_review_ugc.keyword_images import keyword_prompt
    value = keyword_prompt("WELL READ", skill_name="gatorhats-stage1-six-designs")
    assert "embroidered-satin-natural" in value
    assert "TYPOGRAPHY-ONLY" in value
    assert "exactly ONE SOLID THREAD COLOR PER DESIGN" in value
    assert "NO pictorial illustrations" in value
    assert "Vary strictly the font style" in value
    assert "at least THREE must be ILLUSTRATION-LED" not in value
    assert "2–4 THREAD COLOR" not in value
    assert "Tatami fill" in value
    assert "output/final/design_01.png through design_06.png" in value
    assert "WELL READ" in value
    assert "Do not generate, save, upload or" in value