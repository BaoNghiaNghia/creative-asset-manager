"""Immutable Native Master bytes for Muse; never a thumbnail or preview.

The digest is checked against the exact bytes sent, not merely the stored
metadata. No decoding, resizing, conversion, compositing, or public cache.
"""
import hashlib
import re
from fastapi import HTTPException, Response

POLICY = "CAM_MUSE_ORIGINAL_ARTWORK_V1"
MAX_BYTES = 20 * 1024 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def policy():
    return {
        "version": POLICY,
        "assetVariant": "original",
        "artworkKind": "native_master_v2",
        "contentType": "image/png",
        "sha256Header": "X-CAM-Artwork-SHA256",
        "variantHeader": "X-CAM-Artwork-Variant",
        "thumbnailAllowed": False,
        "previewAllowed": False,
        "requireExactByteSha256": True,
        "failureDisposition": "report_ARTWORK_UNAVAILABLE_no_thumbnail_fallback",
        "instructions": (
            "After claiming, fetch the job's authenticated artworkUrl as raw binary. "
            "Use the exact Native Master ORIGINAL PNG, compare the complete bytes' "
            "SHA256 to artworkSha256, and verify the original-variant response header. "
            "Never substitute a design thumbnail, browser preview, screenshot, "
            "product listing photo, resized image, or re-encoded artwork. Use these "
            "original bytes to apply the print to the approved C1717 wearer/hero. "
            "A previously approved hero may be reused only if it is bound to the same "
            "original artwork SHA and current workflow. The isolated transparent "
            "artwork is not itself the image-to-video start frame. If original bytes "
            "are unavailable or wrong, do not generate; report the real error."
        ),
    }


def source(listing_id, digest, review=False):
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        return None
    family = "reviews" if review else "jobs"
    return {
        "policy": POLICY,
        "assetVariant": "original",
        "artworkKind": "native_master_v2",
        "path": f"/api/v1/muse-bridge/{family}/{listing_id}/artwork",
        "expectedSha256": digest,
        "contentType": "image/png",
        "sha256Header": "X-CAM-Artwork-SHA256",
        "variantHeader": "X-CAM-Artwork-Variant",
        "thumbnailFallbackAllowed": False,
        "requiresClaimedVideoJob": not review,
    }


def verified_response(raw, expected_sha, filename):
    if not isinstance(raw, bytes) or len(raw) < 57 or raw[:8] != PNG_MAGIC:
        raise HTTPException(409, "Original Native Master PNG required; no thumbnail fallback")
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, "Original Native Master exceeds the Muse artwork limit")
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise HTTPException(409, "Original Native Master byte SHA256 mismatch; no thumbnail fallback")
    return Response(raw, media_type="image/png", headers={
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f'inline; filename="{filename}"',
        "X-CAM-Artwork-Variant": "original",
        "X-CAM-Artwork-SHA256": expected_sha,
        "X-CAM-Artwork-Policy": POLICY,
    })
