"""CAM typography Stage 1: six final PNGs without Codex or manual review."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image

from app.modules.realistic_review_ugc.stage1_typography_renderer import (
    CANVAS, FONTS, SKILL_NAME, render_six,
)
from app.operations.install_stage1_typography_skill import build_bundle
from app.providers.ai.codex_image import CodexImageGenRunner

ROOT = Path(__file__).resolve().parents[3] / "managed_skills"
FONTS_ROOT = ROOT / "gatorhats-stage1-six-designs" / "fonts"


@pytest.mark.parametrize("keyword", [
    "DUMP HIM", "SUNDAY MORNING CLUB", "STATE OF MIND", "BOY MOM CLUB",
])
def test_exact_six_unique_transparent_artworks(keyword, tmp_path):
    paths = render_six(keyword, FONTS_ROOT, tmp_path)
    assert len(paths) == 6
    assert [p.name for p in paths] == [f"design_{index:02}.png" for index in range(1, 7)]
    assert len({hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}) == 6
    for path in paths:
        with Image.open(path) as image:
            image.load()
            assert image.format == "PNG"
            assert image.mode == "RGBA"
            assert image.size == CANVAS
            assert image.getbbox() is not None
            assert image.getpixel((0, 0))[3] == 0
    assert (tmp_path / "output" / "manifest.json").is_file()
    collected = CodexImageGenRunner._collect_six_final_designs(tmp_path, 6)
    assert [item.filename for item in collected] == [
        f"output/final/design_{i:02}.png" for i in range(1, 7)]


def test_rejects_invalid_or_too_long_quote(tmp_path):
    for invalid in ("", "A" * 70, "\x00 DUMP HIM"):
        with pytest.raises(ValueError):
            render_six(invalid, FONTS_ROOT, tmp_path / "bad")


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


def test_rejects_tampered_or_unexpected_final_checkpoint(tmp_path):
    originals = render_six("DUMP HIM", FONTS_ROOT, tmp_path)
    originals[0].write_bytes(b"corrupt")
    with pytest.raises((ValueError, OSError), match="checkpoint|corrupt|identify"):
        render_six("DUMP HIM", FONTS_ROOT, tmp_path)

    originals[0].unlink()
    (tmp_path / "output" / "final" / "unknown.png").write_bytes(b"unexpected")
    with pytest.raises(ValueError, match="stale"):
        render_six("DUMP HIM", FONTS_ROOT, tmp_path)


def test_local_stage1_skill_bundle_installs_only_verified_fonts():
    pkg = build_bundle()
    assert pkg.skill_name == SKILL_NAME
    names = {item[0] for item in pkg.files}
    assert {"SKILL.md", "manifest.json"}.issubset(names)
    assert all("fonts/" + name in names for name in FONTS)
    assert all((name.startswith("fonts/") or name.startswith("licenses/")
                or name in {"SKILL.md", "manifest.json", "agents/openai.yaml"})
               for name in names)
