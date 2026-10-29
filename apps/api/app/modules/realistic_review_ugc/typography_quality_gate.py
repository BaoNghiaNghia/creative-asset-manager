"""G5 Typography V2 quality gate, isolated from Stage 1 v1.

Policy distinctions:
* SVG previews prove exact *source text* and structural safety only.
* PNG inspection proves pixels/formats/alpha/duplicates, NEVER image spelling.
* Only a distinct, independently human-reviewed final can be eligible in the
  six-slot V2 output contract. No fake, blank or copied filler images.

All functions are deterministic and side-effect-free except bounded read-only
PNG inspection. No database writes, AI providers, Scout or file generation.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import re
from typing import Any, Mapping, Sequence
from xml.etree import ElementTree

from PIL import Image, ImageOps, UnidentifiedImageError

G5_VERSION = "g5-qa/v1"
SLOT_SCHEMA = "typography-v2/slots-v1"
V2_EXPECTED_SLOTS = 6
PNG_DIMENSIONS = (2048, 1024)
MAX_PNG_BYTES = 25 * 1024 * 1024
SVG_NAMESPACE = "{http://www.w3.org/2000/svg}"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
SVG_TAGS = frozenset({"svg", "defs", "path", "text", "textPath"})
# Same Google Fonts OFL names already accepted by the Stage 1 provenance
# manifest. Never allow an arbitrary downloaded font or remote CSS in a
# vector pilot; this is a source check, not a guarantee of browser font loading.
OFL_FAMILY_NAMES = frozenset({
    "Anton", "Bebas Neue", "Pacifico", "Lobster", "Righteous",
    "DM Serif Display", "Alfa Slab One", "Bree Serif",
})
SVG_ATTRIBUTES = frozenset({
    "xmlns", "viewBox", "width", "height", "role", "id", "d", "x", "y",
    "font-family", "font-size", "font-weight", "fill", "text-anchor",
    "textLength", "lengthAdjust", "transform", "startOffset", "href",
    XML_SPACE, "{http://www.w3.org/1999/xlink}href",
})
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class TypographyQualityError(ValueError):
    """Invalid V2 QA inputs or slot contract; no publication may proceed."""


def _safe_xml_rendered_text(svg: Any) -> tuple[str, list[int]]:
    if not isinstance(svg, str) or not 1 <= len(svg) <= 16000:
        raise TypographyQualityError("Missing or oversized vector preview.")
    if "<!" in svg or "<?" in svg:
        raise TypographyQualityError("SVG declarations, doctypes and entities are not permitted.")
    try:
        root = ElementTree.fromstring(svg)
    except ElementTree.ParseError as exc:
        raise TypographyQualityError("Malformed vector preview.") from exc
    if root.tag != SVG_NAMESPACE + "svg":
        raise TypographyQualityError("Expected a namespaced SVG root.")
    if root.attrib.get("viewBox") != "0 0 960 560":
        raise TypographyQualityError("Unexpected preview canvas.")
    sizes: list[int] = []
    chunks: list[str] = []
    for element in root.iter():
        if not isinstance(element.tag, str) or not element.tag.startswith(SVG_NAMESPACE):
            raise TypographyQualityError("Unexpected SVG namespace.")
        local_tag = element.tag[len(SVG_NAMESPACE):]
        if local_tag not in SVG_TAGS or any(attribute not in SVG_ATTRIBUTES for attribute in element.attrib):
            raise TypographyQualityError("Unsafe vector element or attribute.")
        if local_tag == "text":
            family = element.attrib.get("font-family", "")
            if not any(family.startswith(f"'{name}',") for name in OFL_FAMILY_NAMES):
                raise TypographyQualityError("Unapproved preview font family.")
            chunks.append("".join(element.itertext()))
            try:
                size = int(element.attrib["font-size"])
            except (KeyError, ValueError) as exc:
                raise TypographyQualityError("SVG text has no valid font size.") from exc
            if not 1 <= size <= 250:
                raise TypographyQualityError("SVG font size exceeds safe bounds.")
            sizes.append(size)
        href = element.attrib.get("href") or element.attrib.get("{http://www.w3.org/1999/xlink}href")
        if href is not None and (local_tag != "textPath" or href != "#g4-arc"):
            raise TypographyQualityError("External or unexpected SVG link.")
        if any("url(" in value.lower() or "javascript:" in value.lower() for value in element.attrib.values()):
            raise TypographyQualityError("External SVG resource is not permitted.")
    if not 1 <= len(sizes) <= 3:
        raise TypographyQualityError("Expected one to three vector text layers.")
    return "".join(chunks), sizes


def audit_svg_previews(source_keyword_exact: str, previews: Sequence[Mapping[str, Any]]) -> dict:
    """Return technical QA + honest unresolved human-review flags.

    SVG text is a reliable proof of the *vector source*, never a claim that a
    later rasterized/AI-edited image contains visibly correct lettering.
    """
    if not isinstance(source_keyword_exact, str) or not source_keyword_exact.strip():
        raise TypographyQualityError("Missing original keyword.")
    if not isinstance(previews, (list, tuple)) or not 1 <= len(previews) <= 3:
        raise TypographyQualityError("Expected one to three source previews.")
    seen_ids: set[str] = set()
    seen_signatures: set[str] = set()
    items: list[dict] = []
    for index, preview in enumerate(previews, start=1):
        if not isinstance(preview, Mapping):
            raise TypographyQualityError("Invalid vector preview entry.")
        issues: list[str] = []
        flags = ["human_visual_readability_review", "embroidery_scale_unverified",
                 "originality_review_pending"]
        svg = preview.get("svg")
        try:
            rendered, sizes = _safe_xml_rendered_text(svg)
        except TypographyQualityError:
            rendered, sizes = "", []
            issues.append("unsafe_or_invalid_svg")
        if rendered != source_keyword_exact or preview.get("source_keyword_exact") != source_keyword_exact:
            issues.append("source_keyword_mismatch")
        if preview.get("preview_only") is not True or preview.get("eligible_for_generation") is not False:
            issues.append("preview_mislabeled_as_final")
        identity = preview.get("id")
        if not isinstance(identity, str) or not identity or identity in seen_ids:
            issues.append("duplicate_or_invalid_preview_id")
        else:
            seen_ids.add(identity)
        signature = sha256(svg.encode("utf-8")).hexdigest() if isinstance(svg, str) else ""
        if signature in seen_signatures:
            issues.append("duplicate_vector_content")
        else:
            seen_signatures.add(signature)
        if sizes and min(sizes) < 18:
            flags.append("small_vector_lettering")
        if preview.get("layout") == "arched":
            flags.append("arch_readability_requires_visual_check")
        items.append({
            "index": index, "preview_id": identity if isinstance(identity, str) else None,
            "technical_pass": not issues,
            "exact_source_text": rendered == source_keyword_exact and
                                 preview.get("source_keyword_exact") == source_keyword_exact,
            "blocking_codes": list(dict.fromkeys(issues)),
            "review_flags": flags,
            "eligible_for_publication": False,
        })
    return {
        "version": G5_VERSION, "kind": "vector_preview",
        "requested_count": len(previews),
        "technical_pass_count": sum(item["technical_pass"] for item in items),
        "all_technical_pass": all(item["technical_pass"] for item in items),
        "image_spelling_verified": False,
        "human_review_required": True, "release_ready": False,
        "items": items,
    }


def _rgba_dhash(image: Image.Image) -> str:
    # A small stable advisory fingerprint for transparent typography. This
    # cannot prove plagiarism or copied composition; use as review evidence.
    alpha = image.getchannel("A")
    bounds = alpha.getbbox()
    if not bounds:
        return "0" * 16
    crop = image.crop(bounds)
    white = Image.new("RGBA", crop.size, (255, 255, 255, 255))
    white.alpha_composite(crop)
    pixels = ImageOps.grayscale(white.convert("RGB")).resize(
        (9, 8), resample=Image.Resampling.LANCZOS
    )
    val = 0
    for y in range(8):
        for x in range(8):
            val = (val << 1) | int(pixels.getpixel((x, y)) > pixels.getpixel((x + 1, y)))
    return f"{val:016x}"


def inspect_typography_png(data: bytes) -> dict:
    """Validate a single generated final; in-memory inspection only.

    A correct literal keyword in a manifest cannot certify the AI image text.
    """
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_PNG_BYTES:
        return {"technical_pass": False, "blocking_codes": ["invalid_image_size"]}
    digest = sha256(data).hexdigest()
    issues: list[str] = []
    flags: list[str] = []
    footprint = None
    signature = None
    try:
        with Image.open(BytesIO(data)) as opened:
            if opened.format != "PNG" or opened.size != PNG_DIMENSIONS:
                issues.append("invalid_png_canvas")
            if opened.mode != "RGBA":
                issues.append("missing_true_alpha")
            if opened.width * opened.height > 8_000_000:
                issues.append("oversized_pixel_budget")
            if issues:
                return {"technical_pass": False, "blocking_codes": issues,
                        "sha256": digest, "image_spelling_verified": False}
            opened.load()
            image = opened.convert("RGBA")
        alpha = image.getchannel("A")
        bounds = alpha.getbbox()
        if bounds is None:
            issues.append("empty_artwork")
        else:
            left, top, right, bottom = bounds
            footprint = [left, top, right, bottom]
            margin = min(left, top, PNG_DIMENSIONS[0] - right, PNG_DIMENSIONS[1] - bottom)
            if margin < 1:
                issues.append("artwork_touches_canvas_edge")
            if right - left < PNG_DIMENSIONS[0] * 0.08 or bottom - top < PNG_DIMENSIONS[1] * 0.05:
                issues.append("artwork_too_small")
            if right - left > PNG_DIMENSIONS[0] * 0.96 or bottom - top > PNG_DIMENSIONS[1] * 0.96:
                flags.append("artwork_near_oversized_embroidery_area")
            signature = _rgba_dhash(image)
        if alpha.getpixel((0, 0)) != 0:
            issues.append("opaque_background_corner")
        if not issues:
            flags.extend([
                "image_spelling_requires_human_review",
                "min_stitch_and_counter_space_require_physical_scale",
                "image_originality_requires_human_review",
            ])
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        issues.append("png_decode_failed")
    return {
        "technical_pass": not issues, "blocking_codes": list(dict.fromkeys(issues)),
        "review_flags": flags, "sha256": digest, "canvas": list(PNG_DIMENSIONS),
        "alpha_bbox": footprint, "perceptual_hash": signature,
        "image_spelling_verified": False, "eligible_for_publication": False,
    }


def audit_png_slot_set(images: Sequence[tuple[int, bytes]]) -> dict:
    """Inspect 1-6 genuine edited files; never synthesize a missing number."""
    if not isinstance(images, (list, tuple)) or len(images) > V2_EXPECTED_SLOTS:
        raise TypographyQualityError("Invalid V2 generated slot count.")
    seen_slots: set[int] = set()
    hashes: set[str] = set()
    signatures: list[str] = []
    items: list[dict] = []
    for slot, image in images:
        if type(slot) is not int or not 1 <= slot <= V2_EXPECTED_SLOTS or slot in seen_slots:
            raise TypographyQualityError("Duplicate or out-of-range generated slot number.")
        seen_slots.add(slot)
        item = inspect_typography_png(image)
        item["slot"] = slot
        if item.get("sha256") in hashes:
            item["blocking_codes"].append("duplicate_final_sha256")
            item["technical_pass"] = False
        elif item.get("sha256"):
            hashes.add(item["sha256"])
        if item.get("perceptual_hash") and item["technical_pass"]:
            signature = item["perceptual_hash"]
            if any((int(signature, 16) ^ int(other, 16)).bit_count() <= 6 for other in signatures):
                item["review_flags"].append("possible_near_duplicate_review")
            signatures.append(signature)
        items.append(item)
    items.sort(key=lambda obj: obj["slot"])
    return {
        "version": G5_VERSION, "kind": "raster_finals",
        "requested_slots": V2_EXPECTED_SLOTS, "observed_slots": len(images),
        "technical_pass_count": sum(item["technical_pass"] for item in items),
        "human_review_required": True, "release_ready": False,
        "missing_slots": [i for i in range(1, 7) if i not in seen_slots],
        "items": items,
    }


def summarize_v2_slots(source_keyword_exact: str, slots: Sequence[Mapping[str, Any]]) -> dict:
    """G6-ready read-only six-slot contract, no invented final or fake success.

    A generated image becomes eligible only after G5 structural QA AND a
    separate, explicit human visual spelling/design acceptance, never from
    original source text or unverified model claims alone.
    """
    if not isinstance(source_keyword_exact, str) or not source_keyword_exact.strip():
        raise TypographyQualityError("Missing literal source keyword.")
    if not isinstance(slots, (list, tuple)) or len(slots) != V2_EXPECTED_SLOTS:
        raise TypographyQualityError("V2 manifest requires exactly six declared slots.")
    seen: set[int] = set()
    accepted_hashes: set[str] = set()
    eligible: list[int] = []
    failed: list[int] = []
    pending: list[int] = []
    review: list[int] = []
    for entry in slots:
        if not isinstance(entry, Mapping):
            raise TypographyQualityError("Invalid V2 slot entry.")
        number = entry.get("slot")
        if type(number) is not int or number not in range(1, 7) or number in seen:
            raise TypographyQualityError("V2 slot IDs must be unique integers 1-6.")
        seen.add(number)
        state = entry.get("status")
        if state not in ("pending", "failed", "generated"):
            raise TypographyQualityError("Invalid V2 slot state.")
        if state == "pending":
            pending.append(number)
        elif state == "failed":
            failed.append(number)
        else:
            digest = entry.get("sha256")
            if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
                raise TypographyQualityError("Generated slot lacks valid image SHA256.")
            if entry.get("technical_pass") is False or entry.get("review") == "rejected":
                failed.append(number)
            elif (entry.get("technical_pass") is not True or
                    entry.get("review") != "accepted" or
                    entry.get("image_spelling_verified") is not True):
                review.append(number)
            else:
                if digest in accepted_hashes:
                    raise TypographyQualityError("A duplicate image cannot fill two V2 slots.")
                accepted_hashes.add(digest)
                eligible.append(number)
    eligible.sort()
    failed.sort()
    pending.sort()
    review.sort()
    actual_count = len(eligible)
    if actual_count == V2_EXPECTED_SLOTS:
        status = "complete"
    elif pending:
        status = "in_progress" if len(pending) < V2_EXPECTED_SLOTS else "planned"
    elif review:
        status = "needs_review"
    elif actual_count > 0:
        status = "partial"
    else:
        status = "failed"
    return {
        "schema_version": SLOT_SCHEMA, "source_keyword_exact": source_keyword_exact,
        "requested_slots": V2_EXPECTED_SLOTS,
        "eligible_slots": eligible, "failed_slots": failed,
        "pending_slots": pending, "needs_review_slots": review,
        "actual_count": actual_count,
        "partial": 0 < actual_count < V2_EXPECTED_SLOTS,
        "status": status, "completed": status == "complete",
        "publishable": status == "complete" or status == "partial",
        "manufacturing_qa_required": True,
    }
