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
    script = _script()
    row = {**_job().payload_json["design_assets"][1], "color_match": {
        "version": "colormatch-sms-c1717-v6",
        "output_sha256": "e" * 64,
        "recommended": ["black", "sage"],
        "unsafe_colors": ["true-navy"],
        "readability_check": "foreground_ink_connected_components_proxy_v1",
        "insufficient_safe_colors": True,
    }}
    assert script.fresh(row)
    seven = {**row, "color_match": {**row["color_match"], "recommended":
             ["black", "sage", "pepper", "ivory", "blue-jean", "chambray", "butter"],
             "insufficient_safe_colors": False}}
    assert script.fresh(seven)
    zero = {**row, "color_match": {**row["color_match"], "recommended": []}}
    assert script.fresh(zero)
    assert not script.fresh({**row, "color_match": {**row["color_match"], "insufficient_safe_colors": False}})
    assert not script.fresh({**row, "color_match": {
        **row["color_match"], "recommended": ["black", "black"],
    }})
    assert not script.fresh({**row, "color_match": {
        **row["color_match"], "recommended": ["unknown"],
    }})
    assert not script.fresh({**row, "color_match": {
        **row["color_match"], "unsafe_colors": ["black"],
    }})
    assert not script.fresh({**row, "color_match": {
        **row["color_match"], "unsafe_colors": [],
        "readability_check": "untrusted",
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
    assert recommend_garment_colors(preview, checksum)["version"] == "colormatch-sms-c1717-v6"