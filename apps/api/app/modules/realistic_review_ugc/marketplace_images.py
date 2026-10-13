"""Bounded, provenance-safe Etsy/Amazon Stage 0 source-image enrichment.

Never guesses an image from a similar keyword. Exact marketplace listing URL
and an allowlisted official product-image CDN are both required.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import (
    RrugcKeywordVolumeModel, RrugcKeywordAgentOriginModel,
)
from app.modules.realistic_review_ugc.research_intelligence import (
    ResearchValidationError, canonical_marketplace_url,
)
from app.modules.realistic_review_ugc.product_page_import import (
    ProductPageImportError, fetch_product_page,
)

TAVILY = "https://api.tavily.com/search"
IMAGE_HOSTS = {
    "etsy": frozenset({"i.etsystatic.com", "img0.etsystatic.com"}),
    "amazon": frozenset({"m.media-amazon.com", "images-na.ssl-images-amazon.com"}),
}
DAILY_SEARCH_BUDGET = 12


def validate_listing_image(source: str, image_url: str) -> str | None:
    """Allow only official marketplace CDN product images (never logos, avatars)."""
    try:
        url = urlsplit(image_url.strip())
        host = (url.hostname or "").lower().rstrip(".")
        if (source not in IMAGE_HOSTS or url.scheme != "https"
            or url.username or url.password or url.port not in (None, 443)
            or host not in IMAGE_HOSTS[source] or len(image_url) > 2048):
            return None
        path = url.path.casefold()
        if source == "etsy":
            if "/r/il/" not in path or not path.endswith((".jpg", ".jpeg", ".png", ".webp")):
                return None
        elif "/images/i/" not in path or not path.endswith((".jpg", ".jpeg", ".png", ".webp")):
            return None
        return image_url.strip()
    except (ValueError, AttributeError, TypeError):
        return None


def image_from_search_results(source: str, listing_url: str, results: list[dict]) -> str | None:
    """Match result *page* to exact listing ID, not textual relevance alone."""
    try:
        expected = canonical_marketplace_url(source, listing_url)
    except (ResearchValidationError, ValueError):
        return None
    for result in results[:12]:
        if not isinstance(result, dict):
            continue
        try:
            actual = canonical_marketplace_url(source, str(result.get("url") or ""))
        except (ResearchValidationError, ValueError):
            continue
        if actual != expected:
            continue
        raw_images = result.get("images") or []
        # Brave web results expose a single thumbnail attached to the result.
        if isinstance(result.get("thumbnail"), dict):
            thumb = result["thumbnail"]
            raw_images = [thumb.get("original"), thumb.get("src"), *raw_images]
        for image in raw_images[:80]:
            candidate = image.get("url") if isinstance(image, dict) else image
            accepted = validate_listing_image(source, str(candidate or ""))
            if accepted:
                return accepted
    return None


async def _public_listing_image(
    source: str, listing_url: str, client: httpx.AsyncClient,
) -> str | None:
    # Client reads an explicitly fixed official item URL; response redirects
    # remain guarded by the shared SSRF-safe product metadata fetcher.
    try:
        product = await fetch_product_page(client, listing_url)
        if canonical_marketplace_url(source, product.source_url) != listing_url:
            return None
        for img in product.images[:20]:
            valid = validate_listing_image(source, img)
            if valid:
                return valid
    except (ProductPageImportError, ResearchValidationError, ValueError):
        pass
    return None


async def _tavily_listing_image(
    source: str, listing_url: str, client: httpx.AsyncClient,
) -> str | None:
    response = await client.post(
        TAVILY,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Tavily-Access-Mode": "keyless",
            "X-Client-Source": "cam-research-keyless",
        },
        json={
            "query": f'"{listing_url}" embroidered hat',
            "search_depth": "basic",
            "max_results": 8,
            "include_images": True,
            "include_domains": [source + ".com"],
        },
        follow_redirects=False,
    )
    if response.status_code in (402, 403, 429):
        raise RuntimeError("image_search_provider_limited")
    if response.status_code != 200 or len(response.content) > 600_000:
        raise RuntimeError("image_search_unavailable")
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise RuntimeError("image_search_invalid")
    # Tavily's top-level images are not necessarily related to this
    # particular listing. Only per-result images qualify.
    return image_from_search_results(source, listing_url, data["results"])


async def fetch_image_for_source(
    source: str, listing_url: str, client: httpx.AsyncClient,
) -> tuple[str | None, str]:
    try:
        canonical = canonical_marketplace_url(source, listing_url)
    except (ResearchValidationError, ValueError):
        return None, "source_invalid"
    image = await _public_listing_image(source, canonical, client)
    if image:
        return image, "source_metadata"
    try:
        image = await _tavily_listing_image(source, canonical, client)
        if image:
            return image, "source_indexed"
        return None, "metadata_unavailable"
    except RuntimeError as exc:
        return None, "provider_limited" if str(exc) == "image_search_provider_limited" else "source_blocked"
    except (httpx.HTTPError, ValueError):
        return None, "source_blocked"


async def enrich_missing_marketplace_images(
    session: Session, *, tenant_id: str | None = None, limit: int = 6,
    client: httpx.AsyncClient | None = None,
) -> dict[str, int]:
    """Idempotent backfill: preserves volume, TM, user picks and original images.

    Small quota: at most 12 provider probes globally/day, no unlimited retries.
    Next retry per row is 7 days for missing metadata, 24h for transient blocks.
    """
    now = datetime.now(timezone.utc)
    day = now.date().isoformat()
    keyword_rows = session.scalars(select(RrugcKeywordVolumeModel).where(
        RrugcKeywordVolumeModel.source_image_url.is_(None),
        exists(select(RrugcKeywordAgentOriginModel.id).where(
            RrugcKeywordAgentOriginModel.tenant_id == RrugcKeywordVolumeModel.tenant_id,
            RrugcKeywordAgentOriginModel.keyword_normalized == RrugcKeywordVolumeModel.keyword_normalized,
            RrugcKeywordAgentOriginModel.source_platform.in_(("etsy", "amazon")),
            RrugcKeywordAgentOriginModel.source_url.is_not(None),
        )),
        *([RrugcKeywordVolumeModel.tenant_id == tenant_id] if tenant_id else []),
    ).order_by(RrugcKeywordVolumeModel.created_at.asc()).limit(2000)).all()
    if not keyword_rows:
        return {"checked": 0, "updated": 0, "unavailable": 0}
    keys_by_tenant: dict[str, set[str]] = {}
    for row in keyword_rows:
        keys_by_tenant.setdefault(row.tenant_id, set()).add(row.keyword_normalized)
    origins: dict[tuple[str, str], list[RrugcKeywordAgentOriginModel]] = {}
    for tenant, keys in keys_by_tenant.items():
        records = session.scalars(select(RrugcKeywordAgentOriginModel).where(
            RrugcKeywordAgentOriginModel.tenant_id == tenant,
            RrugcKeywordAgentOriginModel.keyword_normalized.in_(keys),
            RrugcKeywordAgentOriginModel.source_platform.in_(("etsy", "amazon")),
            RrugcKeywordAgentOriginModel.source_url.is_not(None),
        ).order_by(RrugcKeywordAgentOriginModel.first_seen_at.asc())).all()
        for record in records:
            origins.setdefault((tenant, record.keyword_normalized), []).append(record)

    # Count every probe, including successful rows no longer in the missing-image query.
    daily_count = sum(
        isinstance(raw, dict) and raw.get("_reach_product_image_checked_day") == day
        for raw in session.scalars(select(RrugcKeywordVolumeModel.provider_raw_json)).all()
    )
    summary = {"checked": 0, "updated": 0, "unavailable": 0}
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=httpx.Timeout(16.0, connect=5.0), trust_env=False)
    try:
        for row in keyword_rows:
            if summary["checked"] >= min(max(0, limit), 12) or daily_count >= DAILY_SEARCH_BUDGET:
                break
            refs = origins.get((row.tenant_id, row.keyword_normalized), [])
            if not refs:
                continue
            raw = dict(row.provider_raw_json or {})
            if raw.get("_reach_product_image_checked_day") == day:
                continue
            until = raw.get("_reach_product_image_retry_after")
            try:
                if until and datetime.fromisoformat(until) > now:
                    continue
            except (ValueError, TypeError):
                pass
            listing = next((ref for ref in refs if ref.source_url), None)
            if not listing:
                continue
            # One source/keyword at a time: prevent a later source replacing an
            # already-approved photo from a different Scout.
            try:
                image, status = await fetch_image_for_source(
                    listing.source_platform, listing.source_url, client
                )
            except (httpx.HTTPError, ValueError):
                image, status = None, "source_blocked"
            summary["checked"] += 1
            daily_count += 1
            if image:
                # A legitimate image URL must match this item's marketplace.
                valid = validate_listing_image(listing.source_platform, image)
                if valid:
                    row.source_image_url = valid
                    raw["_reach_product_image_status"] = "ready"
                    raw["_reach_product_image_source"] = listing.source_url
                    summary["updated"] += 1
                else:
                    raw["_reach_product_image_status"] = "metadata_unavailable"
                    summary["unavailable"] += 1
            else:
                raw["_reach_product_image_status"] = status
                summary["unavailable"] += 1
            raw["_reach_product_image_checked_day"] = day
            raw["_reach_product_image_retry_after"] = (
                now + timedelta(days=7 if status == "metadata_unavailable" else 1)
            ).isoformat()
            row.provider_raw_json = raw
            session.flush()
            if status == "provider_limited":
                break
    finally:
        if owns_client:
            await client.aclose()
    return summary
