"""Trusted on-disk 8869 stock evidence for Ecommerce input preparation.

Stock assets must come from the installed release, not browser-supplied URLs.
This deliberately does not mark them as uploaded managed-storage objects.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

# The frontend's current bundled inventory has only these three verified angles.
STOCK_FILES = {
    "stock_front": "front.jpg",
    "stock_side_90": "side.jpg",
    "stock_back": "back.jpg",
}
MAX_STOCK_BYTES = 15 * 1024 * 1024
MAX_STOCK_PIXELS = 36_000_000


def read_release_stock(role: str, *, project_root: Path) -> tuple[bytes, dict]:
    """Read a bounded, image-validated canonical release stock reference."""
    filename = STOCK_FILES.get(role)
    if filename is None:
        raise ValueError("No verified stock source for requested Ecommerce role")
    root = (project_root / "apps/client/public/rrugc/blueprint/stock/natural-black").resolve()
    target = (root / filename).resolve()
    from app.core.config import get_settings
    from app.modules.realistic_review_ugc.hat_stock_sync import read_managed_stock
    imported = read_managed_stock(get_settings(), "natural-black", filename.removesuffix(".jpg"))
    if imported is not None:
        target = imported[0]
        root = target.parent
    if not target.is_relative_to(root) or not target.is_file():
        raise FileNotFoundError("Required official stock reference is unavailable")
    size = target.stat().st_size
    if not 0 < size <= MAX_STOCK_BYTES:
        raise ValueError("Stock file size invalid")
    content = target.read_bytes()
    if len(content) != size:
        raise ValueError("Stock asset changed during read")
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("Unsupported stock image format")
            width, height = image.size
            if not 0 < width * height <= MAX_STOCK_PIXELS:
                raise ValueError("Stock image dimensions invalid")
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Invalid stock image") from exc
    return content, {"role": role, "sha256": sha256(content).hexdigest(),
                     "size_bytes": size, "width": width, "height": height}


def release_stock_evidence(*, project_root: Path) -> dict[str, dict]:
    """Expose only validated metadata, never absolute filesystem paths."""
    result = {}
    for role in STOCK_FILES:
        try:
            _, metadata = read_release_stock(role, project_root=project_root)
            result[role] = {"ready": True, **metadata}
        except (OSError, ValueError):
            result[role] = {"ready": False, "role": role}
    result["stock_three_quarter"] = {
        "ready": False, "role": "stock_three_quarter",
        "reason": "independent_three_quarter_stock_not_verified",
    }
    return result
