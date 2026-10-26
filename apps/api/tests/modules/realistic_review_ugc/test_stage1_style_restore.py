"""Regression gate for the Stage 1 typography style recovery.

Canonical design identity is strictly independent of material preview photos.
The BEER ME snapshots protect the user's six approved typographic compositions.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import hashlib

from PIL import Image

from app.modules.realistic_review_ugc.stage1_typography_renderer import (
    _render_one, render_six, CANVAS,
)
from app.modules.realistic_review_ugc.stage1_fabric import (
    FABRIC_KEY, extract_thread_geometry, fabric_output_bytes,
    thread_preview_bytes,
)
from app.modules.realistic_review_ugc.ecommerce_front_reference import render_front_reference
from app.modules.realistic_review_ugc.keyword_images import keyword_prompt, LOCAL_TYPOGRAPHY_SKILL

FONTS = Path(__file__).resolve().parents[3] / "managed_skills" / "gatorhats-typography-concepts" / "fonts"

# Baseline: approved original typography before the Fabric-First renderer.
BEER_ME_ALPHA_HASHES = (
    "81729f1a7d445289f6a0", "a06377930a41577e3e8f",
    "4be4027d6b3fbf191882", "7398c87134d530ec7d3e",
    "94f12c8a80c7383e66bb", "ebbe5edfa69ff1ae8ac3",
)


def test_beer_me_approved_six_style_silhouettes_do_not_drift():
    for concept, expected in enumerate(BEER_ME_ALPHA_HASHES, start=1):
        source = _render_one("BEER ME", FONTS, concept)
        digest = hashlib.sha256(source.getchannel("A").tobytes()).hexdigest()[:20]
        assert digest == expected, f"Typography layout drifted in concept {concept}"


def test_stage1_publishes_canonical_transparent_design_not_fabric(tmp_path):
    paths = render_six("BEER ME", FONTS, tmp_path)
    assert len(paths) == 6
    for i, p in enumerate(paths, start=1):
        with Image.open(p) as image:
            image.load()
            assert image.format == "PNG"
            assert image.size == CANVAS
            assert image.mode == "RGBA"
            assert image.getpixel((0, 0))[3] == 0
            assert image.info.get(FABRIC_KEY) is None
            assert image.getchannel("A").tobytes() == _render_one(
                "BEER ME", FONTS, i).getchannel("A").tobytes()
    manifest = (tmp_path / "output" / "manifest.json").read_text()
    assert '"finish": "reference_satin_v4"' in manifest
    assert "fabric_first" not in manifest


def test_old_fabric_versions_still_transfer_only_original_stitches():
    canonical = _render_one("BEER ME", FONTS, 1)
    source = BytesIO()
    canonical.save(source, "PNG")
    legacy_photo = fabric_output_bytes(canonical)
    with Image.open(BytesIO(legacy_photo)) as old:
        assert old.mode == "RGB"
        restored = extract_thread_geometry(old)
        assert restored is not None
        assert restored.getchannel("A").tobytes() == canonical.getchannel("A").tobytes()
    with Image.open(BytesIO(thread_preview_bytes(legacy_photo))) as readback:
        assert readback.mode == "RGBA"
        assert readback.getchannel("A").tobytes() == canonical.getchannel("A").tobytes()
    stock = Image.new("RGB", (1000, 1000), (46, 82, 97))
    st = BytesIO()
    stock.save(st, "PNG")
    assert render_front_reference(st.getvalue(), source.getvalue()) == render_front_reference(
        st.getvalue(), legacy_photo
    )


def test_stage1_skill_prompt_does_not_request_baked_fabric():
    prompt = keyword_prompt("BEER ME", skill_name=LOCAL_TYPOGRAPHY_SKILL)
    assert "transparent canonical embroidery designs" in prompt
    assert "opaque embroidery-on-twill" not in prompt
