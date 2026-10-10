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
    assert "max 640px" in value
    assert "BEACH LIFE" in value
    assert "TEN separate" not in value
    assert "13 ORIGINAL Valucap" not in value
