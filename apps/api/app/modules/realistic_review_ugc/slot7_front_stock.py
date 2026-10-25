"""Isolated, verifiable, versioned front photos for Slot 7 only.

The shared 3-angle hat stock catalogue must never be marked complete using
front-only supplier photos. Full verified managed stock takes priority over
these per-color Slot 7 fallbacks.
"""
from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from .hat_stock_sync import storage_root

PRODUCT_JSON = "https://www.wueinc.com/products/valucap-five-panel-twill-cap-8869.js"
SHOP_FILES_ROOT = "/s/files/1/1567/7353/files/"
# Supplier's exact product variant titles; never trust a random/uncategorized image.
SUPPLIER_VARIANTS = {
    "khaki-maroon": ("Khaki / Maroon", "Khaki-_Maroon"),
    "natural-black": ("Natural / Black", "Natural-_Black"),
    "natural-navy": ("Natural / Navy", "Natural-_Navy"),
    "natural-red": ("Natural / Red", "Natural-_Red"),
}
# Frozen from the supplier's exact style 8869 variant records on 2026-10-25.
# A silently changed remote image requires a new verified release, not an
# automatic replacement of stock pinned into customer-generated images.
EXPECTED_SHA256 = {
    "khaki-maroon": "ddd48427f65abda6b9c3cd2fbdbde0e69a1dc6214d0b3cddfef86eee47858bfe",
    "natural-black": "9374f6f64783d917d058283dba0302ab08d1ec9fd3e2426f01010392fd619325",
    "natural-navy": "f2afb5b4af0a6f8ae0febb85e45fa5c706ba456f2ebafbf6c6ef4710824b5851",
    "natural-red": "d59ae9dd112fbc428ff9aae85d74249a60202beab646471ad64952c1fd8f47c4",
}
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MIN_EDGE = 1000
MAX_PIXELS = 36_000_000


class Slot7StockError(ValueError):
    pass


def slot7_root(settings: Any) -> Path:
    return storage_root(settings) / "slot7-fronts"


def _manifest(root: Path) -> dict:
    try:
        value = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if value.get("version") == 1 and isinstance(value.get("colors"), dict):
            return value
    except (OSError, ValueError, AttributeError):
        pass
    return {"version": 1, "colors": {}}


def configured_slot7_front(settings: Any, color: str) -> bool:
    return color in SUPPLIER_VARIANTS and color in _manifest(slot7_root(settings))["colors"]


def read_slot7_front(settings: Any, color: str) -> tuple[Path, str] | None:
    """Return an immutable, validated path only for a verified imported color."""
    if color not in SUPPLIER_VARIANTS:
        return None
    root = slot7_root(settings)
    entry = _manifest(root)["colors"].get(color)
    if not isinstance(entry, dict):
        return None
    digest = entry.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        return None
    path = root / "sets" / color / digest / "front.jpg"
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        return None
    # The manifest is pinned to content, never silently read a changed file.
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        return None
    return path, digest


def _read_bounded(url: str, *, max_bytes: int, host: str) -> bytes:
    response = urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "CAM-Slot7-StockSync/1"}),
        timeout=18,
    )
    with response:
        resolved = urllib.parse.urlsplit(response.geturl())
        if resolved.scheme != "https" or resolved.hostname != host:
            raise Slot7StockError("Unexpected supplier redirect destination")
        raw = response.read(max_bytes + 1)
    if not 0 < len(raw) <= max_bytes:
        raise Slot7StockError("Supplier response exceeds the allowed size")
    return raw


def fetch_verified_supplier_fronts() -> list[dict]:
    """All four official variants must validate before any persistent writes."""
    try:
        catalog = json.loads(_read_bounded(
            PRODUCT_JSON, max_bytes=6 * 1024 * 1024, host="www.wueinc.com",
        ))
    except (OSError, ValueError) as exc:
        raise Slot7StockError("Unable to verify the supplier product catalog") from exc
    if catalog.get("title") != "Valucap - Five-Panel Twill Cap - 8869":
        raise Slot7StockError("Supplier product does not match Valucap 8869")
    variants = catalog.get("variants")
    if not isinstance(variants, list):
        raise Slot7StockError("Supplier variants not available")
    records = []
    for color, (expected_title, expected_fragment) in SUPPLIER_VARIANTS.items():
        matches = [v for v in variants if v.get("title") == expected_title]
        if len(matches) != 1:
            raise Slot7StockError("Expected product variant missing: " + color)
        variant = matches[0]
        img = variant.get("featured_image")
        url = img.get("src") if isinstance(img, dict) else None
        parsed = urllib.parse.urlsplit(url or "")
        if (
            parsed.scheme != "https" or parsed.hostname != "cdn.shopify.com"
            or not parsed.path.startswith(SHOP_FILES_ROOT)
            or not parsed.path.endswith(".jpg")
            or "Valucap_8869_" not in parsed.path
            or expected_fragment not in parsed.path
            or "Front_High" not in parsed.path
        ):
            raise Slot7StockError("Variant's stock front URL is untrusted: " + color)
        try:
            data = _read_bounded(url, max_bytes=MAX_IMAGE_BYTES, host="cdn.shopify.com")
            with Image.open(io.BytesIO(data)) as pic:
                if pic.format != "JPEG" or min(pic.size) < MIN_EDGE:
                    raise Slot7StockError("Front photo not a high-resolution JPEG: " + color)
                if pic.width * pic.height > MAX_PIXELS:
                    raise Slot7StockError("Stock photo dimensions too large: " + color)
                size = pic.size
                pic.verify()
            with Image.open(io.BytesIO(data)) as pic:
                pic.load()
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise Slot7StockError("Supplier image validation failed: " + color) from exc
        if hashlib.sha256(data).hexdigest() != EXPECTED_SHA256[color]:
            raise Slot7StockError("Supplier image changed since color/photo verification: " + color)
        records.append({
            "color": color, "source_variant": expected_title,
            "supplier_product_id": str(catalog.get("id", "")),
            "supplier_variant_id": str(variant["id"]),
            "url": url, "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "width": size[0], "height": size[1],
            "data": data,
        })
    if len({r["sha256"] for r in records}) != len(SUPPLIER_VARIANTS):
        raise Slot7StockError("Supplier sent duplicate photos for different colors")
    return records


def sync_supplier_fronts(settings: Any, *, apply: bool = False) -> dict:
    """Preview by default. Apply only when all exact color variants pass validation."""
    records = fetch_verified_supplier_fronts()
    root = slot7_root(settings)
    existing = _manifest(root)
    changed = [
        item["color"] for item in records
        if existing["colors"].get(item["color"], {}).get("sha256") != item["sha256"]
    ]
    report = {
        "verified": len(records), "changes": changed,
        "applied": False, "preview": [
            {key: item[key] for key in ("color", "source_variant", "sha256", "bytes", "width", "height", "url")}
            for item in records
        ],
    }
    if not apply:
        return report

    root.mkdir(parents=True, exist_ok=True)
    with (root / ".sync.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest = _manifest(root)
        for item in records:
            digest, color = item["sha256"], item["color"]
            folder = root / "sets" / color / digest
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / "front.jpg"
            if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise Slot7StockError("Existing immutable front differs from recorded hash")
            if not target.is_file():
                descriptor, temporary = tempfile.mkstemp(prefix=".slot7-", dir=folder)
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(item["data"])
                        stream.flush()
                        # The API/worker service is not the root shell that may
                        # run this guarded admin import. Photos are public stock.
                        os.fchmod(stream.fileno(), 0o644)
                        os.fsync(stream.fileno())
                    os.replace(temporary, target)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            manifest["colors"][color] = {
                "sha256": digest, "filename": "front.jpg",
                "source": item["url"], "variant": item["source_variant"],
                "supplier_product_id": item["supplier_product_id"],
                "supplier_variant_id": item["supplier_variant_id"],
                "imported_at": datetime.now(timezone.utc).isoformat(),
            }
        descriptor, temporary = tempfile.mkstemp(prefix=".manifest-", dir=root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(manifest, stream, sort_keys=True, indent=2)
                stream.flush()
                os.fchmod(stream.fileno(), 0o644)
                os.fsync(stream.fileno())
            os.replace(temporary, root / "manifest.json")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        fcntl.flock(lock, fcntl.LOCK_UN)
    report["applied"] = True
    return report
