"""Validated Valucap 8869 photo ZIP import and durable color/angle stock manifest.

No arbitrary filenames, path traversal, partial group replacement or requeue of jobs.
The canonical imported photos live outside immutable production releases.
"""
from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from contextlib import contextmanager
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image, UnidentifiedImageError

COLORS = (
    "khaki-maroon", "natural-black", "natural-brown", "natural-camo-green",
    "natural-charcoal", "natural-forest-green", "natural-khaki", "natural-maroon",
    "natural-mossy-oak-breakup", "natural-navy", "natural-realtree-all-purpose",
    "natural-red", "natural-royal",
)
VIEWS = ("front", "side", "back", "three-quarter")
REQUIRED_VIEWS = ("front", "side", "back")
EXTENSIONS = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
NOISE = {"8869", "valucap", "gatorhats", "hat", "cap", "view", "views", "image", "img",
         "original", "stock", "photo", "photos", "picture", "product"}
MAX_ZIP_BYTES = 120 * 1024 * 1024
MAX_ITEMS = 400
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_UNCOMPRESSED = 240 * 1024 * 1024
MAX_PIXELS = 36_000_000


class HatStockSyncError(ValueError):
    pass


@contextmanager
def stock_read_lock(settings: Any):
    """Read all Ecommerce angles against one stable published stock version."""
    lock_path = storage_root(settings) / ".sync.lock"
    if not lock_path.is_file():
        yield
        return
    with lock_path.open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def storage_root(settings: Any) -> Path:
    """Use the existing persistent CODEX_IMAGE_HOME rather than a release checkout."""
    return Path(str(getattr(settings, "CODEX_IMAGE_HOME", "/var/lib/creative-asset-manager/codex"))).resolve() / "gatorhats-hat-stock"


def _tokens(value: str) -> list[str]:
    value = re.sub(r"(?i)\b3[\s._/-]*4\b", " threequarter ", value)
    value = re.sub(r"(?i)three[\s._/-]*quarters?\b", " threequarter ", value)
    value = re.sub(r"(?i)3[\s._/-]*quarter\b", " threequarter ", value)
    return re.findall(r"[a-z0-9]+", value.lower())


def classify(name: str) -> dict[str, str]:
    """Match exact allowed two-tone names; folders can supply view or color."""
    path = PurePosixPath(name)
    tokens = _tokens("/".join((*path.parts[:-1], path.stem)))
    positions = {
        "front": "front", "frontal": "front", "face": "front",
        "side": "side", "profile": "side", "left": "side", "right": "side",
        "back": "back", "rear": "back", "reverse": "back",
        "threequarter": "three-quarter", "angle": "three-quarter",
    }
    angle_names = {positions[token] for token in tokens if token in positions}
    # Names such as 'Front Right 3/4' are explicitly three-quarter shots.
    if 'three-quarter' in angle_names and 'back' not in angle_names:
        angle_names = {'three-quarter'}
    if len(angle_names) != 1:
        return {"file": name, "error": "missing_or_ambiguous_view"}
    view = next(iter(angle_names))
    words = [token for token in tokens if token not in positions and token not in NOISE]
    # Longest-first prevents natural-black being matched inside longer names.
    # Reject files containing multiple color matches, or a mismatched color suffix.
    color_candidates = []
    for color in COLORS:
        color_words = color.split("-")
        for index in range(len(words) - len(color_words) + 1):
            if words[index:index + len(color_words)] == color_words:
                color_candidates.append((color, index, index + len(color_words)))
    unique_colors = {match[0] for match in color_candidates}
    if len(unique_colors) != 1:
        return {"file": name, "error": "unknown_or_ambiguous_color"}
    color, start, end = next(iter(color_candidates))
    extras = words[:start] + words[end:]
    # Numeric camera/image sequence labels are permitted, but other named colors
    # or product text are not silently discarded.
    if any(not part.isdigit() for part in extras):
        return {"file": name, "error": "unrecognized_filename_tokens"}
    return {"file": name, "color": color, "view": view}


def _validate_image(data: bytes, suffix: str) -> None:
    if not 0 < len(data) <= MAX_IMAGE_BYTES:
        raise HatStockSyncError("image_size_invalid")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ("JPEG", "PNG", "WEBP"):
                raise HatStockSyncError("unsupported_image")
            if image.width * image.height > MAX_PIXELS or min(image.size) <= 0:
                raise HatStockSyncError("image_dimensions_invalid")
            if EXTENSIONS[suffix] != Image.MIME.get(image.format):
                raise HatStockSyncError("image_extension_mismatch")
            image.verify()
        # JPEG integrity is not fully checked by verify(); decode the pixels.
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HatStockSyncError("image_decoding_failed") from exc


def scan_archive(payload: bytes) -> tuple[dict[str, Any], dict[str, dict[str, tuple[str, bytes]]]]:
    if not 0 < len(payload) <= MAX_ZIP_BYTES:
        raise HatStockSyncError("ZIP must be between 1 byte and 120 MB.")
    groups: dict[str, dict[str, list[tuple[str, bytes]]]] = defaultdict(lambda: defaultdict(list))
    issues: list[dict[str, str]] = []
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = [member for member in archive.infolist() if not member.is_dir()]
            if len(entries) > MAX_ITEMS:
                raise HatStockSyncError("Too many files in ZIP (maximum 400).")
            if sum(member.file_size for member in entries) > MAX_UNCOMPRESSED:
                raise HatStockSyncError("ZIP expands beyond the 240 MB safety limit.")
            for entry in entries:
                name = entry.filename
                path = PurePosixPath(name)
                if (name.startswith("/") or "\\" in name or ".." in path.parts
                        or name.startswith("__MACOSX/") or name.endswith("/.DS_Store")):
                    issues.append({"file": name[:180], "error": "ignored_unsafe_or_metadata_path"})
                    continue
                if path.suffix.lower() not in EXTENSIONS:
                    continue
                total += 1
                if entry.file_size > MAX_IMAGE_BYTES:
                    issues.append({"file": name[:180], "error": "image_too_large"})
                    continue
                if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    issues.append({"file": name[:180], "error": "symlink_rejected"})
                    continue
                parsed = classify(name)
                if "error" in parsed:
                    issues.append(parsed)
                    continue
                try:
                    data = archive.read(entry)
                    _validate_image(data, path.suffix.lower())
                except (RuntimeError, OSError, zipfile.BadZipFile, HatStockSyncError) as exc:
                    issues.append({"file": name[:180], "error": "invalid_image"})
                    continue
                groups[parsed["color"]][parsed["view"]].append((name, data))
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise HatStockSyncError("Invalid or corrupt image ZIP.") from exc

    ready: dict[str, dict[str, tuple[str, bytes]]] = {}
    report = []
    for color in COLORS:
        views = groups.get(color)
        if not views:
            continue
        missing = [view for view in REQUIRED_VIEWS if view not in views]
        duplicates = [view for view, files in views.items() if len(files) > 1]
        complete = not missing and not duplicates
        if complete:
            ready[color] = {view: files[0] for view, files in views.items()}
        report.append({
            "color": color,
            "views": {view: [name for name, _ in files] for view, files in views.items()},
            "missing": missing, "duplicates": duplicates, "ready": complete,
        })
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "file_count": total, "ready_count": len(ready),
        "groups": report, "unmatched": issues[:150],
    }, ready


def _manifest(root: Path) -> dict:
    try:
        value = json.loads((root / "manifest.json").read_text(encoding="utf8"))
        return value if value.get("version") == 1 and isinstance(value.get("colors"), dict) else {"version": 1, "colors": {}}
    except (OSError, ValueError, AttributeError):
        return {"version": 1, "colors": {}}


def list_stock(settings: Any) -> dict[str, Any]:
    manifest = _manifest(storage_root(settings))
    return {"colors": {
        color: {"revision": entry["revision"],
                "views": {view: (f"/api/v1/realistic-review-ugc/products/hat-stock/{color}/{view}"
                                 f"?v={entry['revision']}") for view in entry.get("views", {}) if view in VIEWS}}
        for color, entry in manifest["colors"].items()
        if color in COLORS and isinstance(entry, dict) and "revision" in entry
    }}


def read_managed_stock(settings: Any, color: str, view: str) -> tuple[Path, str] | None:
    if color not in COLORS or view not in VIEWS:
        return None
    root = storage_root(settings)
    entry = _manifest(root)["colors"].get(color)
    if not isinstance(entry, dict):
        return None
    filename = entry.get("views", {}).get(view)
    revision = entry.get("revision", "")
    if not isinstance(filename, str) or not re.fullmatch(r"[a-f0-9]{64}", revision):
        return None
    if not re.fullmatch(r"(?:front|side|back|three-quarter)\.(?:jpg|jpeg|png|webp)", filename):
        return None
    path = root / "sets" / color / revision / filename
    if not path.is_file() or not path.resolve().is_relative_to(root):
        return None
    return path, revision


def read_versioned_stock(settings: Any, color: str, view: str, revision: str) -> tuple[Path, str] | None:
    """Serve the exact immutable revision in the thumbnail URL, not a newer set."""
    if color not in COLORS or view not in VIEWS or not re.fullmatch(r"[a-f0-9]{64}", revision):
        return None
    root = storage_root(settings)
    folder = root / "sets" / color / revision
    for ext in EXTENSIONS:
        target = folder / (view + ext)
        if target.is_file() and target.resolve().is_relative_to(root):
            return target, revision
    return None


def apply_archive(payload: bytes, settings: Any, *, expected_sha256: str) -> dict[str, Any]:
    report, ready = scan_archive(payload)
    if report["sha256"] != expected_sha256:
        raise HatStockSyncError("ZIP changed since preview. Preview the exact same file again.")
    if not ready:
        raise HatStockSyncError("No complete color groups found. Nothing was changed.")
    root = storage_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".sync.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest = _manifest(root)
        applied = []
        # Store content-addressed, immutable files first. Only publish the
        # manifest after every selected color is complete and validated.
        for color, views in ready.items():
            digest = hashlib.sha256(b"".join(
                view.encode() + hashlib.sha256(content).digest()
                for view, (_, content) in sorted(views.items())
            )).hexdigest()
            folder = root / "sets" / color / digest
            folder.mkdir(parents=True, exist_ok=True)
            filenames = {}
            for view, (source_name, content) in views.items():
                ext = PurePosixPath(source_name).suffix.lower()
                filename = view + ext
                target = folder / filename
                if not target.exists():
                    descriptor, staging = tempfile.mkstemp(prefix=".hat-", dir=folder)
                    try:
                        with os.fdopen(descriptor, "wb") as stream:
                            stream.write(content)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(staging, target)
                    finally:
                        if os.path.exists(staging):
                            os.unlink(staging)
                filenames[view] = filename
            manifest["colors"][color] = {"revision": digest, "views": filenames}
            applied.append(color)
        descriptor, staging = tempfile.mkstemp(prefix=".manifest-", dir=root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf8") as stream:
                json.dump(manifest, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staging, root / "manifest.json")
        finally:
            if os.path.exists(staging):
                os.unlink(staging)
        fcntl.flock(lock, fcntl.LOCK_UN)
    return {**report, "applied": applied, "applied_count": len(applied)}
