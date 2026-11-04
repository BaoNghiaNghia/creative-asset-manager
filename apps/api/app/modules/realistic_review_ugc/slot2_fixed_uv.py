"""Stage 2 Slot 2: frozen shared-UV mesh projection from the original design PNG.

Based on user-provided hat-grid-system-v1 (2026-11-04).  One exact artwork
atlas is sampled by both the front and 3/4 profiles; no per-view rescale,
optical repositioning, model calls, auto-rotation, dome warp, or recovery of
hidden letters.  The 5x5 UV profile is an ESTIMATE for the supplied hat photos;
it is not a physical 3D reconstruction or calibrated camera model.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

PROFILE_FILE = Path(__file__).with_name("assets") / "slot2_hat_grid_v2.json"
LEGACY_PROFILE_FILE = Path(__file__).with_name("assets") / "slot2_hat_grid_v1.json"
PIPELINE_VERSION = "fixed-shared-uv-v2"
LEGACY_PIPELINE_VERSION = "fixed-shared-uv-v1"
MAX_DESIGN_BYTES = 25_000_000
MAX_STOCK_BYTES = 25_000_000
MAX_SOURCE_PIXELS = 16_000_000
MAX_STOCK_PIXELS = 8_000_000
ATLAS_WIDTH = 1024


class FixedUvProjectionError(ValueError):
    """A pinned profile/PNG/stock cannot be safely rendered; never AI-fallback."""


@dataclass(frozen=True)
class FixedUvProjection:
    output_png: bytes
    front_layer_png: bytes
    profile_sha256: str
    profile_id: str
    uv_mapping_version: str
    atlas_sha256: str
    design_sha256: str
    visible_pixel_count: int
    uv_box: tuple[float, float, float, float]
    target_box: tuple[int, int, int, int]
    visible_fraction: float
    front_anchor: tuple[float, float]
    three_quarter_anchor: tuple[float, float]


def load_profile(expected_profile_sha256: str | None = None) -> tuple[dict, str]:
    """Select immutable pinned v1 or native-front v2, never a mutable remote file."""
    data = PROFILE_FILE.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if expected_profile_sha256 is not None and expected_profile_sha256 != digest:
        legacy = LEGACY_PROFILE_FILE.read_bytes()
        legacy_digest = hashlib.sha256(legacy).hexdigest()
        if expected_profile_sha256 != legacy_digest:
            raise FixedUvProjectionError("Pinned Slot 2 UV profile changed during this job.")
        data, digest = legacy, legacy_digest
    raw = json.loads(data)
    if ((raw.get("schemaVersion"), raw.get("profileId")) not in (
                (1, "cream-navy-user-axis-v1"),
                (2, "white-navy-1254-front-v2"),
            )
            or raw.get("placementDefaults") != {
                "centerU": .5, "centerV": .5,
                "maxWidth": .9, "maxHeight": .85, "trimAlpha": True,
            }
            or raw.get("logicalArea", {}).get("width") != 268
            or raw.get("logicalArea", {}).get("height") != 150):
        raise FixedUvProjectionError("Unrecognized frozen Slot 2 UV profile.")
    rules = raw.get("rules") or {}
    if any(rules.get(key) is not False for key in (
        "autoFitToVisibleMask", "autoRecoverHiddenArtwork", "extraDomeWarp",
    )) or rules.get("extraRotationDegrees") != 0:
        raise FixedUvProjectionError("Slot 2 profile must not rotate, fit or bend design.")
    for key in ("front", "threeQuarter"):
        view = raw["views"][key]
        if (not isinstance(view.get("width"), int)
                or not isinstance(view.get("height"), int)
                or len(view.get("nodes", [])) != 5
                or any(len(row) != 5 for row in view["nodes"])):
            raise FixedUvProjectionError("Frozen UV grid must contain 5 by 5 nodes.")
        for row in view["nodes"]:
            for pt in row:
                if (len(pt) != 2 or not all(isinstance(n, (int, float))
                       and math.isfinite(n) for n in pt)):
                    raise FixedUvProjectionError("Frozen UV grid has invalid coordinates.")
        if not view.get("visibleMaskPath"):
            raise FixedUvProjectionError("Missing frozen UV visibility mask.")
    return raw, digest


def _png_bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, "PNG", optimize=True)
    return output.getvalue()


def _input_design(data: bytes) -> Image.Image:
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_DESIGN_BYTES:
        raise FixedUvProjectionError("Original transparent design PNG is missing/oversized.")
    try:
        with Image.open(BytesIO(data)) as src:
            if (src.format != "PNG" or src.width * src.height > MAX_SOURCE_PIXELS
                    or min(src.size) < 24):
                raise FixedUvProjectionError("Slot 2 requires the original PNG design.")
            src.load()
            rgba = src.convert("RGBA")
    except (OSError, Image.DecompressionBombError) as exc:
        raise FixedUvProjectionError("Original design PNG could not be decoded.") from exc
    lo, hi = rgba.getchannel("A").getextrema()
    if lo == 255 or hi == 0:
        raise FixedUvProjectionError("Design must have a real transparent alpha background.")
    return rgba


def _original_stock(data: bytes) -> Image.Image:
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_STOCK_BYTES:
        raise FixedUvProjectionError("Verified 3/4 stock image is missing or oversized.")
    try:
        with Image.open(BytesIO(data)) as src:
            if (src.format not in ("JPEG", "PNG", "WEBP")
                    or src.width * src.height > MAX_STOCK_PIXELS
                    or min(src.size) < 512):
                raise FixedUvProjectionError("Slot 2 stock photo format/size is unsupported.")
            src.load()
            img = ImageOps.exif_transpose(src).convert("RGB")
    except (OSError, Image.DecompressionBombError) as exc:
        raise FixedUvProjectionError("Verified 3/4 stock photo could not be decoded.") from exc
    return img


def _legacy_texture(source: Image.Image, logical_area: dict) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """Exact v1 placement for already-queued jobs with the old pinned hash."""
    alpha_box = source.getchannel("A").getbbox()
    if alpha_box is None:
        raise FixedUvProjectionError("Design has no nontransparent pixels.")
    left, top, right, bottom = alpha_box
    src_width, src_height = right-left, bottom-top
    lwidth, lheight = logical_area["width"], logical_area["height"]
    scale = min(.90*lwidth/src_width, .85*lheight/src_height)
    width_uv = scale*src_width/lwidth
    height_uv = scale*src_height/lheight
    box = (.5-width_uv/2, .5-height_uv/2,
           .5+width_uv/2, .5+height_uv/2)
    atlas_height = round(ATLAS_WIDTH*lheight/lwidth)
    x0, x1 = int(round(box[0]*ATLAS_WIDTH)), int(round(box[2]*ATLAS_WIDTH))
    y0, y1 = int(round(box[1]*atlas_height)), int(round(box[3]*atlas_height))
    texture = Image.new("RGBA", (ATLAS_WIDTH, atlas_height), (0, 0, 0, 0))
    scaled = source.crop(alpha_box).resize(
        (max(1, x1-x0), max(1, y1-y0)), Image.Resampling.LANCZOS,
    )
    texture.paste(scaled, (x0, y0))
    return np.asarray(texture, dtype=np.uint8), box


def _shared_texture(
    source: Image.Image, logical_area: dict, front_view: dict,
    front_placement: dict,
) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """Crop once, then place painted pixels using the PINNED front field.

    Front and 3/4 sample the same atlas. Neither a destination silhouette nor
    the visible subset of the typography influences this box.
    """
    from app.modules.realistic_review_ugc.front_placement import (
        FrontPlacementInvalid, job_front_placement,
    )

    # Missing / legacy snapshots are NOT an invitation to invent placement.
    if not isinstance(front_placement, dict) or "design_size" not in front_placement:
        raise FixedUvProjectionError("Slot 2 needs the saved FRONT placement and design size.")
    try:
        placement = job_front_placement(front_placement)
    except (FrontPlacementInvalid, KeyError, TypeError, ValueError) as exc:
        raise FixedUvProjectionError("Pinned FRONT placement is invalid.") from exc

    alpha = source.getchannel("A")
    # Identical threshold + one pixel fringe to the painted front footprint.
    ink = alpha.point(lambda value: 255 if value >= 24 else 0)
    bounds = ink.getbbox()
    if bounds is None:
        raise FixedUvProjectionError("Design has no visible alpha pixels.")
    left, top, right, bottom = bounds
    alpha_box = (max(0, left - 1), max(0, top - 1),
                 min(source.width, right + 1), min(source.height, bottom + 1))
    ink_width = alpha_box[2] - alpha_box[0]
    ink_height = alpha_box[3] - alpha_box[1]

    fwidth, fheight = int(front_view["width"]), int(front_view["height"])
    field_width = placement["width"] * fwidth
    field_height = placement["height"] * fheight
    base = min(field_width * .95 / ink_width, field_height * .92 / ink_height)
    zoom = placement["design_size"] / 100
    # The same saved FRONT contain-fit and 98% cap as Blueprint/front renderer.
    scale = base * min(
        zoom, field_width * .98 / (ink_width * base),
        field_height * .98 / (ink_height * base),
    )
    painted_width, painted_height = ink_width * scale, ink_height * scale
    center_x = (placement["left"] + placement["width"] / 2) * fwidth
    center_y = (placement["top"] + placement["height"] / 2) * fheight

    nodes = front_view["nodes"]
    grid_left, grid_right = nodes[2][0][0], nodes[2][4][0]
    grid_top, grid_bottom = nodes[0][2][1], nodes[4][2][1]
    span_x, span_y = grid_right - grid_left, grid_bottom - grid_top
    if span_x <= 0 or span_y <= 0:
        raise FixedUvProjectionError("FRONT grid axes need calibration.")

    # Preserve the saved front center *exactly*: even a 1px intentional
    # offset must survive. A centered design lands on u=0.5 because the
    # front profile's physical middle column is correctly calibrated.
    u_center = (center_x - grid_left) / span_x
    v_center = (center_y - grid_top) / span_y
    u_span, v_span = painted_width / span_x, painted_height / span_y
    box = (u_center - u_span/2, v_center - v_span/2,
           u_center + u_span/2, v_center + v_span/2)
    if not all(math.isfinite(value) for value in box) or (
        min(box) < -1e-8 or max(box) > 1 + 1e-8
    ):
        # Never shrink / move an oversized reference to satisfy a 3/4 mask.
        raise FixedUvProjectionError(
            "Saved FRONT artwork falls outside the calibrated UV grid. "
            "Correct the grid profile, not this design's angle transform."
        )

    lwidth, lheight = logical_area["width"], logical_area["height"]
    atlas_height = round(ATLAS_WIDTH * lheight / lwidth)
    x0 = round(box[0] * ATLAS_WIDTH)
    x1 = round(box[2] * ATLAS_WIDTH)
    y0 = round(box[1] * atlas_height)
    y1 = round(box[3] * atlas_height)
    texture = Image.new("RGBA", (ATLAS_WIDTH, atlas_height), (0, 0, 0, 0))
    # One alpha crop, one resolution conversion, shared by both views.
    scaled = source.crop(alpha_box).resize(
        (max(1, x1-x0), max(1, y1-y0)), Image.Resampling.LANCZOS,
    )
    texture.paste(scaled, (x0, y0))
    return np.asarray(texture, dtype=np.uint8), box


def _mask_outline(path: str) -> np.ndarray:
    """Sample only the SVG M/H/V/L/C/Z commands in the supplied frozen mask."""
    tokens = re.findall(r"[MHLVCZ]|[-+]?(?:\d+(?:\.\d*)?|\.\d+)", path)
    if not tokens or tokens[0] != "M":
        raise FixedUvProjectionError("Invalid frozen visibility mask.")
    result: list[tuple[float, float]] = []
    cursor = (0.0, 0.0)
    start = cursor
    i = 0
    while i < len(tokens):
        cmd = tokens[i]
        i += 1
        if cmd == "M" or cmd == "L":
            x, y = float(tokens[i]), float(tokens[i+1])
            i += 2
            cursor = (x, y)
            if cmd == "M":
                start = cursor
            result.append(cursor)
        elif cmd == "H":
            cursor = (float(tokens[i]), cursor[1])
            i += 1
            result.append(cursor)
        elif cmd == "V":
            cursor = (cursor[0], float(tokens[i]))
            i += 1
            result.append(cursor)
        elif cmd == "C":
            values = [float(n) for n in tokens[i:i+6]]
            i += 6
            p0 = np.asarray(cursor)
            p1, p2, p3 = (np.asarray(values[0:2]), np.asarray(values[2:4]),
                          np.asarray(values[4:6]))
            for t in np.linspace(1/32, 1, 32):
                q = ((1-t)**3*p0 + 3*(1-t)**2*t*p1
                     + 3*(1-t)*t*t*p2 + t**3*p3)
                result.append((float(q[0]), float(q[1])))
            cursor = (float(p3[0]), float(p3[1]))
        elif cmd == "Z":
            result.append(start)
            if i != len(tokens):
                raise FixedUvProjectionError("Unexpected SVG mask commands.")
        else:
            raise FixedUvProjectionError("Unsupported frozen SVG mask command.")
    if len(result) < 4:
        raise FixedUvProjectionError("Empty frozen visibility mask.")
    return np.asarray(result, np.float64)


def _project_atlas(
    texture: np.ndarray, view: dict, size: tuple[int, int], *,
    coverage_stats: dict[str, int] | None = None,
) -> np.ndarray:
    """Vectorized inverse bilinear sampling of the same canonical UV atlas.

    The entire 5x5 frozen grid is traversed; no rotation, optical correction,
    view-specific fitting or dome warp is ever introduced.
    """
    width, height = size
    ref_w, ref_h = int(view["width"]), int(view["height"])
    # A different camera crop/shape cannot be treated as the calibrated view.
    if abs((width/height)/(ref_w/ref_h) - 1) > .15:
        raise FixedUvProjectionError(
            "Stock photo aspect differs from fixed UV profile; recalibrate its view."
        )
    nodes = np.float64(view["nodes"])
    nodes[..., 0] *= width/ref_w
    nodes[..., 1] *= height/ref_h
    mapx = np.full((height, width), -1, dtype=np.float32)
    mapy = np.full((height, width), -1, dtype=np.float32)
    grid_coverage = np.zeros((height, width), dtype=np.uint8)
    tex_h, tex_w = texture.shape[:2]
    for row in range(4):
        for col in range(4):
            p, q = nodes[row, col], nodes[row, col+1]
            r, s = nodes[row+1, col], nodes[row+1, col+1]
            left = max(0, int(np.floor(min(p[0], q[0], r[0], s[0]))) - 1)
            right = min(width, int(np.ceil(max(p[0], q[0], r[0], s[0]))) + 2)
            top = max(0, int(np.floor(min(p[1], q[1], r[1], s[1]))) - 1)
            bottom = min(height, int(np.ceil(max(p[1], q[1], r[1], s[1]))) + 2)
            if left >= right or top >= bottom:
                continue
            yy, xx = np.mgrid[top:bottom, left:right]
            dx, dy = xx-p[0], yy-p[1]
            A, B, C = q-p, r-p, s-q-r+p
            base_det = A[0]*B[1]-A[1]*B[0]
            if base_det <= 1e-8:
                raise FixedUvProjectionError("Frozen UV grid contains a folded cell.")
            a = (dx*B[1]-dy*B[0])/base_det
            b = (A[0]*dy-A[1]*dx)/base_det
            for _ in range(7):
                error_x = A[0]*a+B[0]*b+C[0]*a*b-dx
                error_y = A[1]*a+B[1]*b+C[1]*a*b-dy
                dax, day = A[0]+C[0]*b, A[1]+C[1]*b
                dbx, dby = B[0]+C[0]*a, B[1]+C[1]*a
                determinant = dax*dby-day*dbx
                safe = np.abs(determinant) > 1e-8
                denominator = np.where(safe, determinant, 1)
                a -= np.where(safe, (error_x*dby-error_y*dbx)/denominator, 0)
                b -= np.where(safe, (dax*error_y-day*error_x)/denominator, 0)
            inside = ((a >= 0) & (a <= 1) & (b >= 0) & (b <= 1))
            mx = mapx[top:bottom, left:right]
            my = mapy[top:bottom, left:right]
            mx[inside] = (((col+a[inside])/4)*(tex_w-1)).astype(np.float32)
            my[inside] = (((row+b[inside])/4)*(tex_h-1)).astype(np.float32)
            grid_coverage[top:bottom, left:right][inside] = 1
    # Premultiply BEFORE bilinear interpolation; transparent pixels cannot
    # introduce dark/light halos around letters.
    prepared = texture.astype(np.float32)
    prepared[..., :3] *= prepared[..., 3:4]/255
    sampled = cv2.remap(prepared, mapx, mapy, cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    # Measure design coverage BEFORE occlusion. Published QA then reports
    # how much lettering remains actually visible, not atlas pixel density
    # (which differs with image size and camera foreshortening).
    if coverage_stats is not None:
        coverage_stats["pre_mask_pixels"] = int(np.count_nonzero(
            sampled[..., 3] > 255 * .05,
        ))
    outline = _mask_outline(view["visibleMaskPath"])
    outline[:, 0] *= width/ref_w
    outline[:, 1] *= height/ref_h
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(mask, [np.rint(outline).astype(np.int32)], 255)
    # Exactly alphaOut = alphaDesign * gridCoverage * visibilityMask.
    # The polygon mask has true 0-alpha outside it, not opaque black pixels.
    sampled *= (grid_coverage * (mask / 255.0))[:, :, None]
    return sampled


def render_fixed_uv_slot2(
    design_png: bytes, stock_three_quarter: bytes, *,
    front_placement: dict | None = None,
    expected_profile_sha256: str | None = None,
) -> FixedUvProjection:
    """Return the clean 3/4 Slot 2 output plus a transparent front audit layer."""
    profile, profile_sha = load_profile(expected_profile_sha256)
    source = _input_design(design_png)
    photo = _original_stock(stock_three_quarter)
    legacy = profile["schemaVersion"] == 1
    texture, uv_box = (
        _legacy_texture(source, profile["logicalArea"])
        if legacy else
        _shared_texture(source, profile["logicalArea"], profile["views"]["front"], front_placement)
    )
    atlas_sha = hashlib.sha256(texture.tobytes()).hexdigest()
    front_profile = profile["views"]["front"]
    angle_profile = profile["views"]["threeQuarter"]
    front_size = (front_profile["width"], front_profile["height"])
    front_samples = _project_atlas(texture, front_profile, front_size)
    front_alpha = front_samples[..., 3]/255
    front_colors = np.clip(
        front_samples[..., :3]/np.maximum(front_alpha[..., None], 1e-6),
        0, 255,
    ).astype(np.uint8)
    front_layer = np.dstack((front_colors, np.uint8(np.rint(front_samples[..., 3]))))
    coverage_stats: dict[str, int] = {}
    angular = _project_atlas(
        texture, angle_profile, photo.size, coverage_stats=coverage_stats,
    )
    alpha = np.clip(angular[..., 3]/255, 0, 1)
    count = int(np.count_nonzero(alpha > .05))
    if count < 48:
        raise FixedUvProjectionError(
            "Original design is fully hidden by the saved 3/4 mask; no substitute design."
        )
    ys, xs = np.where(alpha > .05)
    target_box = (
        int(xs.min()), int(ys.min()), int(xs.max()+1), int(ys.max()+1),
    )
    original = np.asarray(photo, np.uint8)
    composited = np.uint8(np.clip(np.rint(
        original.astype(np.float32)*(1-alpha[..., None])
        + angular[..., :3],
    ), 0, 255))
    # All unaffected pixels of the stock remain byte-for-byte equal after
    # decode. JPEG output is never used: every published result is clean PNG.
    if not np.array_equal(composited[alpha == 0], original[alpha == 0]):
        raise FixedUvProjectionError("Fixed UV projection modified protected stock pixels.")
    visible_fraction = float(count/max(1, coverage_stats["pre_mask_pixels"]))
    return FixedUvProjection(
        output_png=_png_bytes(Image.fromarray(composited, "RGB")),
        front_layer_png=_png_bytes(Image.fromarray(front_layer, "RGBA")),
        profile_sha256=profile_sha,
        profile_id=profile["profileId"],
        uv_mapping_version="legacy-centered-v1" if legacy else "saved-front-uv-v2",
        atlas_sha256=atlas_sha,
        design_sha256=hashlib.sha256(design_png).hexdigest(),
        visible_pixel_count=count,
        uv_box=tuple(round(v, 8) for v in uv_box),
        target_box=target_box,
        visible_fraction=round(min(1., visible_fraction), 6),
        front_anchor=(front_profile["nodes"][2][2][0]/front_profile["width"],
                      front_profile["nodes"][2][2][1]/front_profile["height"]),
        three_quarter_anchor=(angle_profile["nodes"][2][2][0]/angle_profile["width"],
                              angle_profile["nodes"][2][2][1]/angle_profile["height"]),
    )
