"""V2 backfill regression: Drive-only source, SHA guard and per-entry idempotence."""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

from app.modules.etsy_spy.color_match import recommend_garment_colors


def _script():
    root = Path(__file__).resolve().parents[5]
    path = root / "scripts" / "cam-etsy-colormatch-v2-backfill.py"
    spec = importlib.util.spec_from_file_location("cam_etsy_v2_backfill_test_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _job():
    sha = "a" * 64
    thumbnail = "b" * 64
    return SimpleNamespace(
        id="11111111-1111-4111-8111-111111111111",
        tenant_id="creative-assets-prod",
        payload_json={"design_assets": [
            {
                "id": "c" * 24, "output_sha256": sha,
                "storage_provider": "google_drive_managed",
                "drive_thumbnail_id": "abcdefghijklmnop",
                "thumbnail_sha256": thumbnail,
                "color_match": {"version": "colormatch-pixel-v1",
                                "output_sha256": sha,
                                "recommended": ["black", "ivory", "sage", "bay"]},
            },
            {
                "id": "d" * 24, "output_sha256": "e" * 64,
                "storage_provider": "google_drive_managed",
                "drive_thumbnail_id": "abcdefghijklmnopq",
                "thumbnail_sha256": thumbnail,
                "color_match": {
                    "version": "colormatch-pixel-v2",
                    "output_sha256": "e" * 64,
                    "recommended": ["black", "sage"],
                },
            },
        ]},
    )


def test_drive_only_metadata_does_not_require_local_storage(monkeypatch):
    monkeypatch.delenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", raising=False)
    script = _script()
    job = _job()
    targets = script.candidate_records(job)
    assert len(targets) == 2
    assert targets[0].provider == "drive"
    assert targets[0].local_path is None
    assert targets[0].thumbnail_sha256 == "b" * 64
    assert not script.still_same({
        **job.payload_json["design_assets"][0],
        "thumbnail_sha256": "f" * 64,
    }, targets[0])
    assert script.still_same(job.payload_json["design_assets"][0], targets[0])


def test_fresh_metadata_validation_requires_known_distinct_colors():
    from io import BytesIO
    script = _script()
    image = Image.new("RGBA", (192, 192), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((15, 15, 150, 150), fill="white")
    buf = BytesIO()
    image.save(buf, format="PNG")
    result = recommend_garment_colors(buf.getvalue(), "e" * 64)
    assert result is not None and result["version"] == "colormatch-sms-c1717-v7"
    row = {**_job().payload_json["design_assets"][1], "color_match": result}
    assert script.fresh(row)
    zero = {**result, "recommended": [], "insufficient_safe_colors": True}
    assert script.fresh({**row, "color_match": zero})
    assert not script.fresh({**row, "color_match": {
        **result, "recommended": ["black", "black"],
    }})
    assert not script.fresh({**row, "color_match": {
        **result, "recommended": ["unknown"],
    }})
    assert not script.fresh({**row, "color_match": {
        **result, "unsafe_colors": ["unknown"],
    }})
    assert not script.fresh({**row, "color_match": {
        **result, "readability_check": "not-v7",
    }})
    assert not script.fresh({**row, "color_match": {
        **result, "analysis": {**result["analysis"], "resolution": [4000, 5000]},
    }})


def test_drive_read_receives_only_signed_thumbnail_reference(monkeypatch):
    script = _script()
    from app.modules.etsy_spy import imagegen_drive

    calls = []

    async def read_thumbnail(**kwargs):
        calls.append(kwargs)
        return b"\x89PNG\r\n\x1a\nthumbnail"

    monkeypatch.setattr(imagegen_drive, "read_design_from_drive", read_thumbnail)
    target = script.candidate_records(_job())[0]
    result = asyncio.run(script.thumbnail_for(target, "creative-assets-prod"))
    assert result is not None
    assert len(calls) == 1
    assert calls[0]["asset_id"].endswith(":thumbnail")
    assert calls[0]["sha256"] == "b" * 64
    assert calls[0]["max_bytes"] == 2 * 1024 * 1024


def test_local_image_checksum_guard(tmp_path):
    script = _script()
    image = Image.new("RGBA", (192, 192), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((15, 15, 150, 150), fill="white")
    path = tmp_path / "art.png"
    image.save(path, format="PNG")
    import hashlib
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    preview = script.make_local_thumbnail(path, checksum)
    assert preview and preview.startswith(b"\x89PNG\r\n\x1a\n")
    assert script.make_local_thumbnail(path, "0" * 64) is None
    assert recommend_garment_colors(preview, checksum)["version"] == "colormatch-sms-c1717-v7"

def test_medium_analysis_is_in_memory_and_has_bounded_dimensions(tmp_path):
    script = _script()
    image = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    ImageDraw.Draw(image).text((90, 90), "SMALL TEXT", fill="#050505")
    buf = __import__("io").BytesIO()
    image.save(buf, format="PNG")
    original = buf.getvalue()
    import hashlib
    digest = hashlib.sha256(original).hexdigest()
    source = tmp_path / "original.png"
    source.write_bytes(original)
    target = script.Target(
        _job().id, "c" * 24, "mapped", digest, "local", local_path=source,
    )
    preview = asyncio.run(script.analysis_preview_for(target, "creative-assets-prod"))
    assert preview and preview[:8] == bytes((137, 80, 78, 71, 13, 10, 26, 10))
    import io
    with Image.open(io.BytesIO(preview)) as result:
        assert result.size == (512, 512)
        assert result.mode == "RGBA"
    assert source.read_bytes() == original
    wrong = script.Target(
        _job().id, "c" * 24, "mapped", "0" * 64, "local", local_path=source,
    )
    assert asyncio.run(script.analysis_preview_for(wrong, "creative-assets-prod")) is None


def test_medium_analysis_drive_original_is_sha_guarded_and_size_limited(monkeypatch):
    script = _script()
    from app.modules.etsy_spy import imagegen_drive
    image = Image.new("RGBA", (768, 768), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((10, 20, 200, 75), fill="#101010")
    import io
    buf = io.BytesIO()
    image.save(buf, "PNG")
    raw = buf.getvalue()
    import hashlib
    sha = hashlib.sha256(raw).hexdigest()
    calls = []

    async def fake_read(**kwargs):
        calls.append(kwargs)
        return raw

    monkeypatch.setattr(imagegen_drive, "read_design_from_drive", fake_read)
    target = script.Target(
        _job().id, "d" * 24, "mapped", sha, "drive",
        drive_original_id="validRemoteOriginalID123",
        original_size=len(raw),
    )
    reduced = asyncio.run(script.analysis_preview_for(target, "creative-assets-prod"))
    assert reduced
    assert len(calls) == 1
    assert calls[0]["sha256"] == sha
    assert calls[0]["asset_id"].endswith(":original")
    assert calls[0]["max_bytes"] == 8 * 1024 * 1024
    big = script.Target(
        _job().id, "d" * 24, "mapped", sha, "drive",
        drive_original_id="validRemoteOriginalID123",
        original_size=9 * 1024 * 1024,
    )
    assert asyncio.run(script.analysis_preview_for(big, "creative-assets-prod")) is None
    assert len(calls) == 1
