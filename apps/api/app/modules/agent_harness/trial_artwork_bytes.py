"""Explicit read-only SHA-256 verification of persisted Stage 1 Drive bytes.

The caller must authorize access and run artifact_lineage before AND after
this operation. This function has no DB/session, reviewer or generation side
effects. Byte transfer costs may occur; guard it behind a default-OFF flag.
"""
from __future__ import annotations

import hashlib
from io import BytesIO
from typing import Any

from PIL import Image, UnidentifiedImageError

from app.domain.providers.contracts import OpenStoredAssetInput
from .trial_artwork_lineage import (
    ArtworkLineageClaim, MAX_ARTWORK_BYTES, TrialArtworkLineageError,
)

_PNG_HEADER = b"\x89PNG\r\n\x1a\n"


async def read_verified_artwork_png(storage: Any, claim: ArtworkLineageClaim) -> tuple[dict, bytes]:
    """Return bounded original PNG only after independent bytes + decode verification."""
    stream = await storage.open_asset(OpenStoredAssetInput(
        tenant_id=claim.tenant_id,
        asset_id=(
            "rrugc-keyword-image:" + claim.image_job_id
            + ":" + claim.image_processing_job_id + ":verification"
        ),
        remote_file_id=claim.remote_file_id,
        content_type="image/png",
        size_bytes=claim.size_bytes,
    ))
    chunks = []
    count = 0
    digest = hashlib.sha256()
    try:
        async for chunk in stream.body:
            if not isinstance(chunk, bytes):
                raise TrialArtworkLineageError("trial_artifact_stream_invalid")
            count += len(chunk)
            if count > MAX_ARTWORK_BYTES or count > claim.size_bytes:
                raise TrialArtworkLineageError("trial_artifact_size_changed")
            digest.update(chunk)
            chunks.append(chunk)
    finally:
        await stream.close()

    if count != claim.size_bytes or digest.hexdigest() != claim.sha256:
        raise TrialArtworkLineageError("trial_artifact_digest_mismatch")
    payload = b"".join(chunks)
    if not payload.startswith(_PNG_HEADER):
        raise TrialArtworkLineageError("trial_artifact_not_png")
    try:
        with Image.open(BytesIO(payload)) as picture:
            if picture.format != "PNG" or picture.size != (claim.width, claim.height):
                raise TrialArtworkLineageError("trial_artifact_dimensions_changed")
            picture.verify()
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError):
        raise TrialArtworkLineageError("trial_artifact_png_corrupt") from None

    status = {
        "output_version_id": claim.output_version_id,
        "lineage_status": "database_and_storage_verified",
        "brief_treatment_receipt_found": True,
        "image_processing_receipt_found": True,
        "storage_bytes_verified": True,
        "stored_artifact_sha256": claim.sha256,
        "size_bytes_verified": count,
        "independent_qa_verified": False,
        "eligible_for_a_b_evaluation": False,
        "evaluation_blockers": [
            "independent_blinded_reviewer_unavailable",
            "trusted_provider_cost_unavailable",
        ],
    }
    return status, payload


async def verify_stored_artwork_bytes(storage: Any, claim: ArtworkLineageClaim) -> dict:
    """Existing read-only API: never expose raw image bytes in the response."""
    status, _ = await read_verified_artwork_png(storage, claim)
    return status
