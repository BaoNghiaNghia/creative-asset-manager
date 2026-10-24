from __future__ import annotations

import io
import zipfile
from types import SimpleNamespace

import pytest
from PIL import Image

from app.modules.realistic_review_ugc.hat_stock_sync import (
    HatStockSyncError, apply_archive, classify, list_stock, read_managed_stock, read_versioned_stock,
    scan_archive,
)


def photo() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (48, 60), (220, 207, 190)).save(output, "JPEG")
    return output.getvalue()


def archive(items: list[str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in items:
            bundle.writestr(name, photo())
    return output.getvalue()


def test_classifies_color_name_from_filename_or_folder():
    assert classify("8869/Natural Black Front.jpg")["color"] == "natural-black"
    assert classify("Front/Natural_Black.JPG")["view"] == "front"
    assert classify("Natural-Navy/3.4.jpeg")["view"] == "three-quarter"
    assert classify("Natural/Black/Back.jpg")["color"] == "natural-black"
    assert classify("Natural/Black/Back.jpg")["view"] == "back"
    assert classify("Natural Blue Front.jpg")["error"] == "unknown_or_ambiguous_color"


def test_preview_reports_missing_and_duplicate_without_mixing():
    data = archive([
        "Natural Black Front.jpg", "Natural Black Side.jpg", "Natural Black Back.jpg",
        "Natural Navy Front.jpg", "Natural Navy Back.jpg",
        "Natural Red Front.jpg", "Natural Red Front 2.jpg", "Natural Red Side.jpg",
        "Natural Red Back.jpg",
        "Unknown Color Side.jpg",
    ])
    report, ready = scan_archive(data)
    assert report["ready_count"] == 1
    assert list(ready) == ["natural-black"]
    by_color = {group["color"]: group for group in report["groups"]}
    assert by_color["natural-navy"]["missing"] == ["side"]
    assert by_color["natural-red"]["duplicates"] == ["front"]
    assert report["unmatched"][0]["error"] == "unknown_or_ambiguous_color"


def test_partial_import_is_atomic_versioned_and_keeps_existing(tmp_path):
    settings = SimpleNamespace(CODEX_IMAGE_HOME=str(tmp_path))
    good = archive(["Natural Black Front.jpg", "Natural Black Side.jpg", "Natural Black Back.jpg"])
    preview, _ = scan_archive(good)
    with pytest.raises(HatStockSyncError, match="changed since preview"):
        apply_archive(good, settings, expected_sha256="0" * 64)
    assert read_managed_stock(settings, "natural-black", "front") is None
    result = apply_archive(good, settings, expected_sha256=preview["sha256"])
    assert result["applied_count"] == 1
    first = read_managed_stock(settings, "natural-black", "front")
    assert first is not None and first[0].read_bytes() == photo()
    assert "?v=" in list_stock(settings)["colors"]["natural-black"]["views"]["front"]
    incomplete = archive(["Natural Navy Front.jpg", "Natural Navy Back.jpg"])
    preview2, _ = scan_archive(incomplete)
    with pytest.raises(HatStockSyncError, match="No complete color groups"):
        apply_archive(incomplete, settings, expected_sha256=preview2["sha256"])
    assert read_managed_stock(settings, "natural-black", "front") == first
    # Old immutable thumbnails remain available after a later manifest swap.
    (tmp_path / "gatorhats-hat-stock" / "manifest.json").write_text('{"version":1,"colors":{}}')
    assert read_managed_stock(settings, "natural-black", "front") is None
    assert read_versioned_stock(settings, "natural-black", "front", first[1]) == first
    assert read_versioned_stock(settings, "../../", "front", first[1]) is None


def test_rejects_path_traversal_and_bad_image(tmp_path):
    payload = archive([
        "../../Natural Black Front.jpg",
        "Natural Black Side.jpg",
        "Natural Black Back.jpg",
    ])
    report, _ = scan_archive(payload)
    assert report["ready_count"] == 0
    assert any(entry["error"] == "ignored_unsafe_or_metadata_path" for entry in report["unmatched"])
    with pytest.raises(HatStockSyncError, match="Invalid or corrupt"):
        scan_archive(b"not zip")
