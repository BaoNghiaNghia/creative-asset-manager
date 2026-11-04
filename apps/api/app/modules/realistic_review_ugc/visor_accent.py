"""Products Slot 7: one cached ML color/shape accent mask, 12 deterministic visor palettes.

No image provider or network request is used for accent selection or recoloring.
Masks are derived from the immutable photographic Slot 1 embroidery material.
All geometry/alpha outside the chosen thread-only mask remains unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO

import cv2
import numpy as np
from PIL import Image

from .threadtransfer import _layer_from_reference

VERSION = "visor-matched-accent-v1"
MAX_ACCENT_RATIO = 0.30
MIN_CONTRAST = 2.5
MIN_MAIN_DELTA_LAB = 19.0

from .madeira_visor_threads import (
    MADEIRA_VISOR_THREADS, PALETTE_VERSION as MADEIRA_PALETTE_VERSION,
    UNMAPPED_VISORS,
)

# Immutable previous palette for already queued v3 jobs, without a color pin.
# Older RGB estimates are not certified Madeira yarn colors.
LEGACY_PALETTE_VERSION = "visor-thread-approximation-v1"
LEGACY_VISOR_THREAD_RGB: dict[str, tuple[int, int, int]] = {
    "natural-black": (58, 61, 66),
    "natural-brown": (110, 69, 44),
    "natural-camo-green": (74, 92, 52),
    "natural-charcoal": (83, 87, 94),
    "natural-forest-green": (38, 86, 61),
    "natural-khaki": (110, 79, 48),
    "natural-maroon": (127, 46, 63),
    "natural-mossy-oak-breakup": (91, 78, 45),
    "natural-navy": (36, 65, 111),
    "natural-realtree-all-purpose": (111, 78, 53),
    "natural-red": (159, 44, 49),
    "natural-royal": (45, 81, 157),
}
# New batches use exact supplied Madeira digital RGB for ten named visors.
# Two print-only visors still have NO verified Madeira mapping and use their
# documented legacy RGB preview only, never a fabricated yarn SKU.
VISOR_THREAD_RGB: dict[str, tuple[int, int, int]] = {
    **LEGACY_VISOR_THREAD_RGB,
    **{key: thread.rgb for key, thread in MADEIRA_VISOR_THREADS.items()},
}
assert UNMAPPED_VISORS == set(VISOR_THREAD_RGB) - set(MADEIRA_VISOR_THREADS)


class VisorAccentError(ValueError):
    """An unsafe or corrupted accent mask/contrast must never be published."""


@dataclass(frozen=True)
class AccentSelection:
    mask_png: bytes
    method: str
    selected_pixels: int
    thread_pixels: int
    layer_sha256: str

    @property
    def ratio(self) -> float:
        return self.selected_pixels / max(1, self.thread_pixels)


def _png_mask(mask: np.ndarray) -> bytes:
    buffer = BytesIO()
    Image.fromarray(mask.astype(np.uint8), "L").save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def _mask_array(mask_png: bytes, shape: tuple[int, int]) -> np.ndarray:
    try:
        with Image.open(BytesIO(mask_png)) as image:
            if image.format != "PNG" or image.size != (shape[1], shape[0]):
                raise VisorAccentError("Cached accent mask does not match the immutable thread layer.")
            image.load()
            return np.asarray(image.convert("L"), dtype=np.uint8)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise VisorAccentError("Cached accent mask cannot be decoded safely.") from exc


def _layer_bytes(layer: Image.Image) -> str:
    return sha256(layer.convert("RGBA").tobytes()).hexdigest()


def _lab_rgb(rgb: tuple[int, int, int]) -> np.ndarray:
    return cv2.cvtColor(np.uint8([[rgb]]), cv2.COLOR_RGB2LAB)[0, 0].astype(np.float32)


def _contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    def luma(rgb: tuple[int, int, int]) -> float:
        v = np.asarray(rgb, dtype=np.float64) / 255.0
        v = np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
        return float(np.dot(v, [0.2126, 0.7152, 0.0722]))
    x, y = sorted((luma(a), luma(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)


def _choose_component_mask(ink: np.ndarray) -> np.ndarray | None:
    """Choose complete lettering/motif components; never slice through a glyph."""
    h, w = ink.shape
    closed = cv2.morphologyEx(ink.astype(np.uint8), cv2.MORPH_CLOSE,
                              np.ones((3, 3), dtype=np.uint8))
    count, labels, stats, centers = cv2.connectedComponentsWithStats(closed, 8)
    total = max(1, int(ink.sum()))
    choices = []
    for index in range(1, count):
        current = (labels == index) & ink
        pixels = int(current.sum())
        fraction = pixels / total
        if not 0.04 <= fraction <= MAX_ACCENT_RATIO:
            continue
        cx, cy = centers[index]
        center_distance = abs(cx / w - 0.5) + abs(cy / h - 0.50)
        choices.append((abs(fraction - 0.17) + center_distance * 0.22, current))
    if not choices:
        return None
    choices.sort(key=lambda item: item[0])
    return choices[0][1]


def select_accent(layer: Image.Image) -> AccentSelection:
    """Unsupervised LAB k-means selects a chromatically distinct design feature.

    Monochrome artwork instead uses complete connected glyph/motif components.
    ML is executed once, persisted and reused across all 12 visor colors.
    If no complete safe component exists, a zero mask is cached and reported;
    no arbitrary part of a letter is damaged just to force a color change.
    """
    rgba = np.asarray(layer.convert("RGBA"), dtype=np.uint8)
    ink = rgba[:, :, 3] >= 95
    thread_pixels = int(ink.sum())
    if thread_pixels < 180:
        raise VisorAccentError("Saved reference has too few embroidery pixels.")
    lab = cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_RGB2LAB)
    sample = lab[ink].astype(np.float32)
    selection = None
    method = "no-safe-accent"
    if thread_pixels >= 600:
        indices = np.linspace(0, thread_pixels - 1, min(6000, thread_pixels), dtype=int)
        training = sample[indices].copy()
        # Fixed initialization makes the same material deterministic across processes.
        distribution = training[:, 0].argsort(kind="stable")
        labels = np.empty((len(training), 1), dtype=np.int32)
        labels[distribution[:len(training)//3]] = 0
        labels[distribution[len(training)//3:2*len(training)//3]] = 1
        labels[distribution[2*len(training)//3:]] = 2
        try:
            _compactness, _groups, centers = cv2.kmeans(
                training, 3, labels, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 18, 0.4),
                1, cv2.KMEANS_USE_INITIAL_LABELS,
            )
            closest = np.argmin(np.sum((sample[:, None, :] - centers[None, :, :]) ** 2, axis=2), axis=1)
            counts = np.bincount(closest, minlength=3)
            dominant = int(np.argmax(counts))
            choices = []
            for idx in range(3):
                if idx == dominant:
                    continue
                fraction = counts[idx] / thread_pixels
                center_delta = float(np.linalg.norm(centers[idx] - centers[dominant]))
                chroma_delta = float(np.linalg.norm(centers[idx, 1:] - centers[dominant, 1:]))
                if not (0.04 <= fraction <= MAX_ACCENT_RATIO and center_delta >= 25
                        and (chroma_delta >= 14 or center_delta >= 47)):
                    continue
                candidate = np.zeros(ink.shape, dtype=bool)
                candidate[ink] = closest == idx
                # Reject texture glints and speckles (not a coherent accent).
                n, components, stats, _ = cv2.connectedComponentsWithStats(
                    candidate.astype(np.uint8), 8,
                )
                largest = int(stats[1:, cv2.CC_STAT_AREA].max()) if n > 1 else 0
                if largest < max(30, int(counts[idx] * 0.07)):
                    continue
                score = abs(fraction - 0.18) - chroma_delta / 800
                choices.append((score, candidate))
            if choices:
                choices.sort(key=lambda item: item[0])
                selection = choices[0][1]
                method = "lab-kmeans-chromatic"
        except cv2.error:
            # Non-generative component fallback never changes source artwork.
            pass

    if selection is None:
        selection = _choose_component_mask(ink)
        if selection is not None:
            method = "connected-lettering"

    if selection is None:
        selection = np.zeros(ink.shape, dtype=bool)

    selected = int(selection.sum())
    if selected > int(thread_pixels * MAX_ACCENT_RATIO):
        raise VisorAccentError("Selected embroidery exceeds the 30% ink-area limit.")
    mask = np.uint8(selection) * 255
    return AccentSelection(
        mask_png=_png_mask(mask), method=method, selected_pixels=selected,
        thread_pixels=thread_pixels, layer_sha256=_layer_bytes(layer),
    )


def selection_from_reference(reference_png: bytes) -> AccentSelection:
    layer, _count = _layer_from_reference(reference_png)
    return select_accent(layer)


def recolor_layer(
    layer: Image.Image,
    selection: AccentSelection,
    color_key: str,
    stock: Image.Image,
    guide_box: tuple[int, int, int, int],
    *,
    palette_version: str = MADEIRA_PALETTE_VERSION,
) -> tuple[Image.Image, dict]:
    """Recolor only the chosen accent; pin exact yarn shade for new jobs."""
    if color_key not in VISOR_THREAD_RGB:
        raise VisorAccentError("Unknown Products Slot 7 visor color.")
    if palette_version not in (MADEIRA_PALETTE_VERSION, LEGACY_PALETTE_VERSION):
        raise VisorAccentError("Unsupported pinned visor thread palette.")
    matched_thread = (
        MADEIRA_VISOR_THREADS.get(color_key)
        if palette_version == MADEIRA_PALETTE_VERSION else None
    )
    rgba = np.asarray(layer.convert("RGBA"), dtype=np.uint8)
    if _layer_bytes(layer) != selection.layer_sha256:
        raise VisorAccentError("Accent selection does not match the saved embroidery reference.")
    mask = _mask_array(selection.mask_png, rgba.shape[:2]) >= 128
    ink = rgba[:, :, 3] >= 95
    selected = int(np.count_nonzero(mask & ink))
    if (int(np.count_nonzero(mask & ~ink)) > 0
            or selected != selection.selected_pixels
            or int(ink.sum()) != selection.thread_pixels
            or selected > int(ink.sum() * MAX_ACCENT_RATIO)):
        raise VisorAccentError("Invalid or over-limit accent mask.")
    meta = {
        "version": VERSION, "method": selection.method,
        "mask_sha256": sha256(selection.mask_png).hexdigest(),
        "layer_sha256": selection.layer_sha256,
        "selected_pixels": selected, "thread_pixels": selection.thread_pixels,
        "accent_ratio": round(selection.ratio, 5),
        "max_ratio": MAX_ACCENT_RATIO, "color_key": color_key,
        "thread_palette_version": palette_version,
        "madeira_thread": ({
            "code": matched_thread.code, "name": matched_thread.name,
            "hex": matched_thread.hex,
        } if matched_thread else None),
        "thread_mapping": (
            "verified_code" if matched_thread else "legacy_unverified_approximation"
        ),
    }
    if selected == 0:
        return layer, {**meta, "status": "skipped_no_safe_region", "changed_pixels": 0}
    x0, y0, x1, y1 = guide_box
    stock_rgb = np.asarray(stock.convert("RGB"), dtype=np.uint8)
    sample = stock_rgb[max(0,y0):min(stock_rgb.shape[0],y1),
                       max(0,x0):min(stock_rgb.shape[1],x1)]
    if not sample.size:
        raise VisorAccentError("Pinned cap embroidery field is invalid.")
    crown = tuple(int(n) for n in np.median(sample.reshape(-1, 3), axis=0))
    main_pixels = rgba[:, :, :3][ink & ~mask]
    if main_pixels.size < 60:
        raise VisorAccentError("Accent would leave too little original thread.")
    dominant = tuple(int(n) for n in np.median(main_pixels, axis=0))
    base = (LEGACY_VISOR_THREAD_RGB if palette_version == LEGACY_PALETTE_VERSION
            else VISOR_THREAD_RGB)[color_key]
    candidates = [base]
    # Legacy unverified approximations may adjust luminance for contrast.
    # Verified Madeira SKUs must NEVER be silently diluted into nonexistent
    # physical yarn colors. If unsafe, keep the immutable original artwork.
    if matched_thread is None:
        for portion in (.14, .28, .43, .57, .70):
            candidates.append(tuple(int(round(channel * (1 - portion))) for channel in base))
            candidates.append(tuple(int(round(channel * (1 - portion) + 255 * portion))
                                    for channel in base))
    chosen = None
    for candidate in candidates:
        contrast = _contrast(candidate, crown)
        delta = float(np.linalg.norm(_lab_rgb(candidate) - _lab_rgb(dominant)))
        if contrast >= MIN_CONTRAST and delta >= MIN_MAIN_DELTA_LAB:
            chosen = (candidate, contrast, delta)
            break
    if chosen is None:
        if matched_thread is not None:
            return layer, {
                **meta, "status": "skipped_insufficient_contrast",
                "changed_pixels": 0, "visor_palette_rgb": list(base),
                "texture_preserved": True, "stock_pixels_preserved": True,
            }
        raise VisorAccentError("Visor thread has insufficient contrast against the crown or main yarn.")
    target, contrast, delta = chosen

    # Preserve fine-thread luminance fluctuations instead of flattening selected
    # yarn to a solid synthetic RGB rectangle.
    original = rgba[:, :, :3]
    lum = cv2.cvtColor(original, cv2.COLOR_RGB2GRAY).astype(np.float32)
    median = max(20, float(np.median(lum[mask & ink])))
    stitch_lighting = np.clip(lum / median, 0.68, 1.32)
    painted = np.clip(np.asarray(target, dtype=np.float32)[None, None, :] *
                      stitch_lighting[:, :, None], 0, 255)
    result = rgba.copy()
    result[:, :, :3][mask] = np.uint8(np.rint(painted[mask]))
    if not np.array_equal(result[:, :, 3], rgba[:, :, 3]) or not np.array_equal(
        result[:, :, :3][~mask], original[~mask],
    ):
        raise VisorAccentError("Accent changed original thread alpha or non-accent pixels.")
    changed = int(np.count_nonzero(np.any(result[:, :, :3] != original, axis=2) & mask))
    if changed < selected * 0.35:
        raise VisorAccentError("Accent recoloring did not produce a visible change.")
    return Image.fromarray(result, "RGBA"), {
        **meta, "status": "applied", "changed_pixels": changed,
        "thread_rgb": list(target), "visor_palette_rgb": list(base),
        "crown_contrast": round(contrast, 3),
        "main_thread_lab_delta": round(delta, 2),
        "texture_preserved": True, "stock_pixels_preserved": True,
    }
