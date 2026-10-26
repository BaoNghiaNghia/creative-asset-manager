"""Two front-only derived Natural stock images from the user's approved 12-hat set.

Does not mutate the complete 4-angle catalog, artwork, or generated outputs.
The source images and method are pinned: changing the imported reference set
requires a new review, not an automatic recolor of potentially unrelated hats.
"""
from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from .hat_stock_sync import read_managed_stock
from .slot7_front_stock import _manifest, read_slot7_front, slot7_root

ORIGIN = "derived-approved-12-hats-v1"
# Exact customer-supplied, imported 12-hat photo bytes, audited 2026-10-26.
PINNED_INPUTS = {
    "natural-brown": "d3df097a41eb39dd3928d2635e627105c5fc5b5ddb3e2507a9dc30dce9a71a4a",
    "white-black": "4b6b5412d4a1b48cab0e8d9742481b92806f45a921dac4deb2eae0d2ada280fe",
    "white-navy": "49f047c705fd6127d6975d66894249678e4f9b191683c035b7cbf69606c8d5ba",
}
DERIVED_TARGETS = {
    "natural-black": "white-black",
    "natural-navy": "white-navy",
}
TINT = np.array([0.990, 0.923, 0.853], dtype=np.float32)
SOURCE_DIMENSIONS = (1254, 1254)


class DerivedFrontError(RuntimeError):
    pass


def _pinned_reference(settings: Any, color: str) -> bytes:
    source = read_managed_stock(settings, color, "front")
    if source is None:
        raise DerivedFrontError("Missing approved 12-hat source: " + color)
    raw = source[0].read_bytes()
    if hashlib.sha256(raw).hexdigest() != PINNED_INPUTS[color]:
        raise DerivedFrontError("Customer 12-hat reference changed: " + color)
    with Image.open(io.BytesIO(raw)) as im:
        if im.size != SOURCE_DIMENSIONS:
            raise DerivedFrontError("Unexpected reference dimensions: " + color)
        im.verify()
    return raw


def render_natural_front(white_stock: bytes) -> bytes:
    """Colorize only the light crown, keeping the exact visor/button and logo.

    The 12-hat white-stock images have a near-white background. Fill the
    connected cap silhouette, protect dark thread/visor and fade the mask
    above the visor shadows so no colored halo is painted onto the background.
    """
    with Image.open(io.BytesIO(white_stock)) as im:
        if im.size != SOURCE_DIMENSIONS:
            raise DerivedFrontError("Reference dimensions must be 1254x1254")
        frame = np.asarray(im.convert("RGB"), dtype=np.float32)
    silhouette = np.uint8(np.max(255 - frame, axis=2) > 10)
    silhouette = cv2.morphologyEx(
        silhouette, cv2.MORPH_CLOSE, np.ones((15, 15), dtype=np.uint8))
    contours, _ = cv2.findContours(
        silhouette, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours or cv2.contourArea(max(contours, key=cv2.contourArea)) < 100000:
        raise DerivedFrontError("Approved stock cap silhouette could not be isolated")
    alpha = np.zeros(frame.shape[:2], dtype=np.float32)
    cv2.drawContours(
        alpha, [max(contours, key=cv2.contourArea)], -1, color=1, thickness=-1)
    alpha = cv2.GaussianBlur(alpha, (7, 7), 1.5)
    lightness = np.min(frame, axis=2)
    y = np.arange(frame.shape[0], dtype=np.float32)[:, None]
    # 145/175 protects black/navy visor and the dark GatorHats side embroidery.
    # Fade at y=850..875 to exclude white ground/cast shadows below the bill.
    alpha *= np.clip((lightness - 145) / 30, 0, 1)
    alpha *= np.clip((875 - y) / 25, 0, 1)
    tinted = np.clip(frame * TINT, 0, 255)
    recolored = np.uint8(
        np.clip(frame * (1 - alpha[:, :, None]) +
                tinted * alpha[:, :, None], 0, 255))
    buffer = io.BytesIO()
    Image.fromarray(recolored, "RGB").save(
        buffer, format="JPEG", quality=95, subsampling=0, optimize=True)
    return buffer.getvalue()


def reconcile_new_hat_fronts(settings: Any, *, apply: bool = False) -> dict:
    """Idempotent, atomic importer; preview is always read-only.

    Does not requeue any AI generation and retains all old immutable photos.
    """
    # Verify every reference before touching the persistent manifest.
    for key in PINNED_INPUTS:
        _pinned_reference(settings, key)
    records = {}
    for color, white_color in DERIVED_TARGETS.items():
        raw = render_natural_front(_pinned_reference(settings, white_color))
        digest = hashlib.sha256(raw).hexdigest()
        records[color] = {
            "sha256": digest, "bytes": len(raw), "image": raw,
            "source_color": white_color,
            "source_sha256": PINNED_INPUTS[white_color],
        }
    if len({r["sha256"] for r in records.values()}) != len(records):
        raise DerivedFrontError("Both cap colorways resolved to the same image")

    root = slot7_root(settings)
    existing = _manifest(root)["colors"]
    changed = [key for key, r in records.items()
               if existing.get(key, {}).get("sha256") != r["sha256"]]
    report = {
        "mode": "apply" if apply else "preview",
        "origin": ORIGIN, "reference_color": "natural-brown",
        "reference_sha256": PINNED_INPUTS["natural-brown"],
        "changes": changed,
        "images": {
            k: {"sha256": r["sha256"], "bytes": r["bytes"],
                "dimensions": list(SOURCE_DIMENSIONS),
                "source_color": r["source_color"],
                "source_sha256": r["source_sha256"]}
            for k, r in records.items()
        },
        "applied": False,
    }
    if not apply:
        return report

    root.mkdir(parents=True, exist_ok=True)
    with (root / ".sync.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest = _manifest(root)
        now = datetime.now(timezone.utc).isoformat()
        for color, item in records.items():
            digest = item["sha256"]
            destination = root / "sets" / color / digest
            destination.mkdir(parents=True, exist_ok=True)
            photo = destination / "front.jpg"
            if photo.exists() and hashlib.sha256(photo.read_bytes()).hexdigest() != digest:
                raise DerivedFrontError("Immutable stock revision content mismatch: " + color)
            if not photo.exists():
                handle, temporary = tempfile.mkstemp(prefix=".natural-", dir=destination)
                try:
                    with os.fdopen(handle, "wb") as stream:
                        stream.write(item["image"])
                        stream.flush()
                        os.fchmod(stream.fileno(), 0o644)
                        os.fsync(stream.fileno())
                    os.replace(temporary, photo)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            previous = manifest["colors"].get(color, {})
            if previous.get("sha256") == digest and previous.get("origin") == ORIGIN:
                continue
            manifest["colors"][color] = {
                "sha256": digest, "filename": "front.jpg",
                "origin": ORIGIN, "source_color": item["source_color"],
                "source_sha256": item["source_sha256"],
                "reference_color": "natural-brown",
                "reference_sha256": PINNED_INPUTS["natural-brown"],
                "imported_at": now,
            }
        handle, temporary = tempfile.mkstemp(prefix=".manifest-", dir=root)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(manifest, stream, sort_keys=True, indent=2)
                stream.flush()
                os.fchmod(stream.fileno(), 0o644)
                os.fsync(stream.fileno())
            os.replace(temporary, root / "manifest.json")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        fcntl.flock(lock, fcntl.LOCK_UN)
    for color, item in records.items():
        installed = read_slot7_front(settings, color)
        if installed is None or installed[1] != item["sha256"]:
            raise DerivedFrontError("Post-import checksum verification failed: " + color)
    report["applied"] = True
    return report
