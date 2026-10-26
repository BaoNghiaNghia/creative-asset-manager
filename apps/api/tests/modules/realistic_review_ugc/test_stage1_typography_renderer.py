"""CAM typography Stage 1: six final PNGs without Codex or manual review."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image
from app.modules.realistic_review_ugc.stage1_fabric import extract_thread_geometry

from app.modules.realistic_review_ugc.stage1_typography_renderer import (
    CANVAS, FONTS, SKILL_NAME, render_six,
)
from app.operations.install_stage1_typography_skill import build_bundle
from app.providers.ai.codex_image import CodexImageGenRunner

ROOT = Path(__file__).resolve().parents[3] / "managed_skills"
FONTS_ROOT = ROOT / "gatorhats-typography-concepts" / "fonts"


@pytest.mark.parametrize("keyword", [
    "DUMP HIM", "SUNDAY MORNING CLUB", "STATE OF MIND", "BOY MOM CLUB",
])
def test_exact_six_unique_opaque_fabric_designs(keyword, tmp_path):
    paths = render_six(keyword, FONTS_ROOT, tmp_path)
    assert len(paths) == 6
    assert [p.name for p in paths] == [f"design_{index:02}.png" for index in range(1, 7)]
    assert len({hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}) == 6
    for path in paths:
        with Image.open(path) as image:
            image.load()
            assert image.format == "PNG"
            assert image.mode == "RGB"
            assert image.size == CANVAS
            assert image.getbbox() is not None
            assert extract_thread_geometry(image).getchannel("A").getbbox() is not None
    assert (tmp_path / "output" / "manifest.json").is_file()
    collected = CodexImageGenRunner._collect_six_final_designs(tmp_path, 6)
    assert [item.filename for item in collected] == [
        f"output/final/design_{i:02}.png" for i in range(1, 7)]


def test_rejects_invalid_or_too_long_quote(tmp_path):
    for invalid in ("", "A" * 70, "\x00 DUMP HIM"):
        with pytest.raises(ValueError):
            render_six(invalid, FONTS_ROOT, tmp_path / "bad")



def test_extra_verified_google_fonts_manifest_entries_do_not_block_renderer(tmp_path):
    import json
    import shutil
    from app.modules.realistic_review_ugc.stage1_typography_renderer import _verify_fonts
    destination = tmp_path / "typography"
    shutil.copytree(FONTS_ROOT.parent, destination)
    manifest = destination / "font_sources.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    extra = dict(data["fonts"][0])
    extra["file"] = "Additional-Official-Font.ttf"
    data["fonts"].append(extra)
    manifest.write_text(json.dumps(data), encoding="utf-8")
    _verify_fonts(destination / "fonts")


def test_missing_required_google_font_still_fails(tmp_path):
    import json
    import shutil
    from app.modules.realistic_review_ugc.stage1_typography_renderer import _verify_fonts
    destination = tmp_path / "typography"
    shutil.copytree(FONTS_ROOT.parent, destination)
    manifest = destination / "font_sources.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["fonts"] = [item for item in data["fonts"] if item["file"] != FONTS[0]]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="required entries are missing"):
        _verify_fonts(destination / "fonts")


def test_fails_closed_when_fonts_missing(tmp_path):
    (tmp_path / "fonts").mkdir()
    with pytest.raises(ValueError, match="font"):
        render_six("DUMP HIM", tmp_path / "fonts", tmp_path / "workspace")


def test_resume_identical_checkpoint_without_overwrite(tmp_path):
    originals = render_six("DUMP HIM", FONTS_ROOT, tmp_path)
    prior_bytes = originals[0].read_bytes()
    originals[1].unlink()
    resumed = render_six("DUMP HIM", FONTS_ROOT, tmp_path)
    assert len(resumed) == 6
    assert originals[0].read_bytes() == prior_bytes
    assert originals[1].is_file()


def test_recovers_corrupt_final_without_deleting_evidence(tmp_path):
    originals = render_six("DUMP HIM", FONTS_ROOT, tmp_path)
    expected = originals[0].read_bytes()
    originals[0].write_bytes(b"corrupt")
    repaired = render_six("DUMP HIM", FONTS_ROOT, tmp_path)
    assert repaired[0].read_bytes() == expected
    archived = list((tmp_path / "output" / ".stale-checkpoints").glob("*/design_01.png"))
    assert len(archived) == 1 and archived[0].read_bytes() == b"corrupt"

    originals[0].unlink()
    (tmp_path / "output" / "final" / "unknown.png").write_bytes(b"unexpected")
    with pytest.raises(ValueError, match="stale"):
        render_six("DUMP HIM", FONTS_ROOT, tmp_path)




@pytest.mark.parametrize("keyword", ["STATE OF MIND", "CHAOS COORDINATOR", "HAMBURGER HELP ME"])
def test_sample_layouts_have_real_thread_ridges_and_correct_colors(keyword, tmp_path):
    from app.modules.realistic_review_ugc.stage1_typography_renderer import _render_one
    from PIL import ImageStat
    paths = render_six(keyword, FONTS_ROOT, tmp_path)
    assert len(paths) == 6
    for index, path in enumerate(paths, start=1):
        flat = _render_one(keyword, FONTS_ROOT, index)
        with Image.open(path) as output:
            geometry = extract_thread_geometry(output)
            assert geometry.getchannel("A").tobytes() == flat.getchannel("A").tobytes()
            # Inside ink, a solid printed glyph has a single flat RGB value,
            # while real satin rows must have visible high/low thread ridges.
            alpha = geometry.getchannel("A")
            interior = alpha.point([255 if value > 240 else 0 for value in range(256)])
            variance = ImageStat.Stat(geometry.getchannel("B"), mask=interior).stddev[0]
            assert variance > 3.0, (index, variance)
            thumbnail = geometry.resize((205, 102), Image.Resampling.LANCZOS)
            thumbnail_ink = thumbnail.getchannel("A").point(
                [255 if value > 220 else 0 for value in range(256)]
            )
            thumbnail_ridges = ImageStat.Stat(
                thumbnail.getchannel("B"), mask=thumbnail_ink
            ).stddev[0]
            assert thumbnail_ridges > 4.0, (index, thumbnail_ridges)
            assert output.mode == "RGB"
    # C3 red script must be above the larger blue support line (as in
    # the reference concept photos), not under a full-blue script.
    with Image.open(paths[2]) as c3:
        rgb = extract_thread_geometry(c3)
        reds = 0
        upper_reds = 0
        for y in range(180, 540, 5):
            for x in range(180, 1870, 5):
                r, g, b, a = rgb.getpixel((x, y))
                if a > 150 and r > b * 1.25 and r > g * 1.2:
                    reds += 1
                    if y < 520:
                        upper_reds += 1
        assert reds > 10
        assert upper_reds / reds > 0.9



@pytest.mark.parametrize("keyword", ["STATE OF MIND", "CHAOS COORDINATOR", "HAMBURGER HELP ME"])
def test_sample_layouts_have_thread_ridges_and_correct_colors(keyword, tmp_path):
    from app.modules.realistic_review_ugc.stage1_typography_renderer import _render_one
    from PIL import ImageStat
    paths = render_six(keyword, FONTS_ROOT, tmp_path)
    assert len(paths) == 6
    for index, path in enumerate(paths, start=1):
        flat = _render_one(keyword, FONTS_ROOT, index)
        with Image.open(path) as output:
            geometry = extract_thread_geometry(output)
            assert geometry.getchannel("A").tobytes() == flat.getchannel("A").tobytes()
            # Satin rows vary in brightness; printed fills are flat RGB.
            alpha = geometry.getchannel("A")
            interior = alpha.point([255 if value > 240 else 0 for value in range(256)])
            variance = ImageStat.Stat(geometry.getchannel("B"), mask=interior).stddev[0]
            assert variance > 3.0, (index, variance)
            assert output.mode == "RGB"
    # C3 red script must be above the blue block line, as in the samples.
    with Image.open(paths[2]) as c3:
        thread = extract_thread_geometry(c3)
        reds = 0
        upper_reds = 0
        for y in range(180, 540, 5):
            for x in range(180, 1870, 5):
                r, g, b, a = thread.getpixel((x, y))
                if a > 150 and r > b * 1.25 and r > g * 1.2:
                    reds += 1
                    if y < 520:
                        upper_reds += 1
        assert reds > 10
        assert upper_reds / reds > 0.9

def test_typography_no_dafont_assets_or_settings():
    assert all("dafont" not in name.lower() for name in FONTS)
    package = build_bundle()
    assert all("dafont" not in name.lower() for name, *_ in package.files)
    assert "font_sources.json" in {name for name, *_ in package.files}
    assert sum(name.startswith("licenses/") for name, *_ in package.files) == len(FONTS)


def test_local_stage1_skill_bundle_installs_only_verified_fonts():
    pkg = build_bundle()
    assert pkg.skill_name == SKILL_NAME
    names = {item[0] for item in pkg.files}
    assert {"SKILL.md", "manifest.json"}.issubset(names)
    assert all("fonts/" + name in names for name in FONTS)
    assert all((name.startswith("fonts/") or name.startswith("licenses/") or name.startswith("references/")
                or name in {"SKILL.md", "manifest.json", "font_sources.json", "agents/openai.yaml"} )
               for name in names)