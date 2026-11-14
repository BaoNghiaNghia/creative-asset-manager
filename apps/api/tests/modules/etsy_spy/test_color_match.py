"""ColorMatch: bounded thumbnail-only recommendation and safe JSON projection."""
from __future__ import annotations

import hashlib
import io
from types import SimpleNamespace

from PIL import Image, ImageDraw

from app.modules.etsy_spy.color_match import ALGORITHM, GARMENT_COLORS, recommend_garment_colors
from app.modules.etsy_spy.design_artifacts import project_design_assets


def _png(ink: str, *, empty: bool = False) -> bytes:
    image = Image.new("RGBA", (192, 192), (0, 0, 0, 0))
    if not empty:
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 26, 174, 160), fill=ink)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_white_artwork_avoids_white_and_black_artwork_avoids_black():
    sha = "a" * 64
    light = recommend_garment_colors(_png("#ffffff"), sha)
    dark = recommend_garment_colors(_png("#151515"), sha)
    assert light and dark
    assert light["version"] == ALGORITHM
    assert light["output_sha256"] == sha
    assert 8 <= len(light["recommended"]) <= 10
    assert len(dark["recommended"]) >= 8
    assert "white" not in light["recommended"]
    assert "black" not in dark["recommended"]
    allowed = {name for name, _ in GARMENT_COLORS}
    assert set(light["recommended"]).issubset(allowed)
    assert len(set(light["recommended"])) == len(light["recommended"])


def test_transparent_or_corrupt_artwork_falls_back_without_blocking():
    assert recommend_garment_colors(_png("white", empty=True), "a" * 64) is None
    assert recommend_garment_colors(b"not a png", "a" * 64) is None
    assert recommend_garment_colors(_png("black"), "invalid digest") is None
    assert recommend_garment_colors(b"X" * (2 * 1024 * 1024 + 1), "a" * 64) is None


def test_picked_projection_serves_only_fresh_valid_cached_metadata():
    sha = hashlib.sha256(b"master-bytes").hexdigest()
    metadata = recommend_garment_colors(_png("#ffffff"), sha)
    assert metadata
    raw = dict(
        id="a" * 24, position="front", method="imagegen", variant=1,
        output_sha256=sha, color_match=metadata,
    )
    job = SimpleNamespace(
        id="job-id", entity_id="listing", payload_json={"design_assets": [raw]},
    )
    output = project_design_assets(job)
    assert output[0]["color_match"]["recommended"] == metadata["recommended"]
    assert output[0]["color_match"]["output_sha256"] == sha
    assert "scores" not in output[0]["color_match"]  # keep Picked API small
    assert output[0]["color_match"]["version"] == "colormatch-pixel-v2"
    # Historical V1 metadata remains usable until bounded local backfill.
    legacy = {**metadata, "version": "colormatch-pixel-v1",
              "recommended": metadata["recommended"][:4]}
    job.payload_json["design_assets"][0]["color_match"] = legacy
    assert project_design_assets(job)[0]["color_match"]["version"] == "colormatch-pixel-v1"
    job.payload_json["design_assets"][0]["color_match"] = metadata
    job.payload_json["design_assets"][0]["color_match"] = {
        **metadata, "output_sha256": "b" * 64,
    }
    assert "color_match" not in project_design_assets(job)[0]
    job.payload_json["design_assets"][0]["color_match"] = {
        **metadata, "recommended": ["white", "../../path"],
    }
    assert "color_match" not in project_design_assets(job)[0]
    job.payload_json["design_assets"][0]["color_match"] = metadata
    job.payload_json["design_assets"][0]["output_sha256"] = "b" * 64
    assert "color_match" not in project_design_assets(job)[0]


def test_thumbnail_color_analysis_does_not_change_native_master_png_bytes():
    from app.modules.etsy_spy.imagegen_drive import _thumbnail_with_colormatch
    image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((20, 25, 200, 210), fill="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    png = buffer.getvalue()
    before = hashlib.sha256(png).hexdigest()
    preview, dimensions, metadata = _thumbnail_with_colormatch(
        png, digest=before, native_master=True,
    )
    assert dimensions == (256, 256)
    assert preview[:8] == bytes((137, 80, 78, 71, 13, 10, 26, 10))
    assert metadata and metadata["output_sha256"] == before
    assert metadata["recommended"]
    assert hashlib.sha256(png).hexdigest() == before


def test_legacy_candidate_preview_includes_verified_multiple_recommendations():
    sha = hashlib.sha256(b"historical-candidate").hexdigest()
    metadata = recommend_garment_colors(_png("#ffffff"), sha)
    assert metadata and len(metadata["recommended"]) > 1
    job = SimpleNamespace(
        id="job-id", entity_id="listing",
        payload_json={"review_candidate": {
            "status": "review_candidate", "output_sha256": sha,
            "method": "local_layered", "color_match": metadata,
        }},
    )
    entry = project_design_assets(job)[0]
    assert entry["id"] == "candidate"
    assert entry["color_match"]["recommended"] == metadata["recommended"]
    assert "scores" not in entry["color_match"]
    job.payload_json["review_candidate"]["color_match"] = {
        **metadata, "output_sha256": "b" * 64,
    }
    assert "color_match" not in project_design_assets(job)[0]


def test_legacy_fast_preview_can_include_verified_recommendations():
    sha = hashlib.sha256(b"historical-fast").hexdigest()
    metadata = recommend_garment_colors(_png("#151515"), sha)
    job = SimpleNamespace(
        id="job-id", entity_id="listing",
        payload_json={"result": {
            "status": "needs_review", "output_sha256": sha,
            "color_match": metadata,
        }},
    )
    entry = project_design_assets(job)[0]
    assert entry["id"] == "fast"
    assert entry["color_match"]["recommended"] == metadata["recommended"]


def test_local_backfill_requires_matching_png_sha_and_preserves_source(tmp_path):
    import importlib.util
    from pathlib import Path

    path = (Path(__file__).resolve().parents[5]
            / "scripts" / "cam-etsy-colormatch-local-backfill.py")
    spec = importlib.util.spec_from_file_location("cam_etsy_colormatch_local_backfill", path)
    assert spec and spec.loader
    import types
    backfill = types.ModuleType(spec.name)
    spec.loader.exec_module(backfill)
    file = tmp_path / "historical-design.png"
    original = _png("#ffffff")
    file.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()
    preview = backfill.make_thumbnail(file, digest)
    assert preview and preview[:8] == bytes((137, 80, 78, 71, 13, 10, 26, 10))
    assert backfill.make_thumbnail(file, "f" * 64) is None
    assert file.read_bytes() == original
