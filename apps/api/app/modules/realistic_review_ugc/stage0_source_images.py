"""Operator-supplied marketplace preview images with tenant and listing provenance.

A CDN URL only proves that an image exists, not that it belongs to a product.
An operator must select the exact recorded listing when associating a source
image with a Stage 0 keyword; preserve all preexisting metric fields.
"""
from __future__ import annotations
from datetime import datetime, timezone
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .marketplace_images import validate_listing_image
from .research_intelligence import ResearchValidationError, canonical_marketplace_url
from .model import RrugcKeywordVolumeModel, RrugcKeywordAgentOriginModel


class Stage0SourceImageError(ValueError):
    pass


async def attach_marketplace_image(
    session: Session, *,
    tenant_id: str,
    keyword_id: str,
    listing_url: str,
    image_url: str,
    actor_id: str,
    replace: bool = False,
    client: httpx.AsyncClient | None = None,
) -> RrugcKeywordVolumeModel:
    row = session.scalar(select(RrugcKeywordVolumeModel).where(
        RrugcKeywordVolumeModel.tenant_id == tenant_id,
        RrugcKeywordVolumeModel.id == keyword_id,
    ).with_for_update())
    if row is None:
        raise LookupError("Keyword not found")
    if row.source_image_url and not replace:
        raise Stage0SourceImageError("Source image exists; explicit replace required")

    origins = session.scalars(select(RrugcKeywordAgentOriginModel).where(
        RrugcKeywordAgentOriginModel.tenant_id == tenant_id,
        RrugcKeywordAgentOriginModel.keyword_normalized == row.keyword_normalized,
        RrugcKeywordAgentOriginModel.source_platform.in_(("etsy", "amazon")),
        RrugcKeywordAgentOriginModel.source_url.is_not(None),
    )).all()
    match = None
    for origin in origins:
        try:
            canonical = canonical_marketplace_url(origin.source_platform, listing_url)
            recorded = canonical_marketplace_url(origin.source_platform, origin.source_url)
        except (ResearchValidationError, ValueError):
            continue
        if canonical == recorded:
            match = origin
            break
    if match is None:
        raise Stage0SourceImageError("Choose a previously recorded Amazon/Etsy listing for this keyword")

    image = validate_listing_image(match.source_platform, image_url)
    if not image:
        raise Stage0SourceImageError("Image must be a public product JPEG/PNG/WebP on the matching marketplace CDN")

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=httpx.Timeout(9, connect=4), trust_env=False)
    try:
        # The allowlist above prevents SSRF. Do not follow CDN redirects to
        # arbitrary hosts. Never download full image bytes to the API worker.
        try:
            response = await client.head(image, follow_redirects=False)
        except httpx.HTTPError as exc:
            raise Stage0SourceImageError("Could not verify the public image URL") from exc
        if response.status_code != 200 or not response.headers.get("content-type", "").lower().startswith("image/"):
            raise Stage0SourceImageError("Image URL did not return a public image (HTTP 200)")
        length = response.headers.get("content-length", "")
        if length.isdigit() and int(length) > 25_000_000:
            raise Stage0SourceImageError("Image is too large")
    finally:
        if owns_client:
            await client.aclose()

    raw = dict(row.provider_raw_json or {})
    row.source_image_url = image
    raw["_reach_product_image_status"] = "operator_supplied"
    raw["_reach_product_image_source"] = canonical_marketplace_url(match.source_platform, listing_url)
    raw["_reach_product_image_verified_at"] = datetime.now(timezone.utc).isoformat()
    raw["_reach_product_image_verified_by"] = actor_id
    row.provider_raw_json = raw
    session.commit()
    session.refresh(row)
    return row
