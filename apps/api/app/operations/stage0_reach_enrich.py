"""Bounded, independent Stage 0 enrichment for paired VPS Agent-Reach sources.

A public product link may expose a verified thumbnail in structured HTML.
Never synthesize images, use search-result thumbnails as product photographs,
bypass marketplace blocks, follow arbitrary domains, or rewrite known metrics.
AEBrowse work is a single low-rate batch of pending candidates per cycle.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.modules.realistic_review_ugc.model import RrugcKeywordAgentOriginModel, RrugcKeywordVolumeModel
from app.modules.realistic_review_ugc.keyword_volume import (
    KEYWORD_VOLUME_PENDING_PROVIDER, KeywordVolumeError, RrugcKeywordVolumeService,
)
from app.modules.realistic_review_ugc.product_page_import import (
    ProductPageImportError, fetch_product_image, fetch_product_page,
)
from app.modules.realistic_review_ugc.research_discovery import AGENT_NAME
from app.modules.realistic_review_ugc.research_intelligence import canonical_marketplace_url, ResearchValidationError

LOGGER = logging.getLogger(__name__)
ATTEMPT_IMAGE_KEY = "_reach_product_image_attempt_at"
IMAGE_STATE_KEY = "_reach_product_image_status"
ATTEMPT_METRICS_KEY = "_reach_metrics_attempt_at"
# Only first-party/known marketplace CDN hosts can become Preview URLs.
IMAGE_HOSTS = {
    "amazon": frozenset({"m.media-amazon.com", "images-na.ssl-images-amazon.com", "images-eu.ssl-images-amazon.com"}),
    "etsy": frozenset({"i.etsystatic.com", "img.etsystatic.com"}),
    "pinterest": frozenset({"i.pinimg.com"}),
}
IMAGE_RETRY_AFTER = timedelta(hours=24)
METRICS_RETRY_AFTER = timedelta(hours=6)


def checked_marketplace_source(platform: str, url: str) -> str | None:
    if platform not in IMAGE_HOSTS:
        return None
    try:
        # Exact official hosts and product/Pin paths. No arbitrary Agent input.
        return canonical_marketplace_url(platform, url)
    except ResearchValidationError:
        return None


def select_image_url(platform: str, images: list[str]) -> list[str]:
    hosts = IMAGE_HOSTS.get(platform, frozenset())
    seen: set[str] = set()
    result: list[str] = []
    for image in images:
        parts = urlsplit(image)
        host = (parts.hostname or "").lower()
        if (parts.scheme != "https" or host not in hosts or parts.username or parts.password
                or parts.port not in (None, 443) or len(image) > 2048
                or image in seen or not parts.path):
            continue
        seen.add(image)
        result.append(image)
        if len(result) >= 3:
            break
    return result


def due(raw: dict | None, key: str, now: datetime, gap: timedelta) -> bool:
    value = (raw or {}).get(key)
    if not isinstance(value, str):
        return True
    try:
        last = datetime.fromisoformat(value)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return last <= now - gap
    except ValueError:
        return True


def pending_rows(session: Session, *, image_limit: int, metrics_limit: int, now: datetime):
    # The query is tenant-safe: evidence and keyword MUST share tenant and
    # normalized keyword. No cross-tenant titles/URLs can leak.
    rows = session.execute(
        select(RrugcKeywordVolumeModel, RrugcKeywordAgentOriginModel)
        .join(RrugcKeywordAgentOriginModel, (
            (RrugcKeywordVolumeModel.tenant_id == RrugcKeywordAgentOriginModel.tenant_id)
            & (RrugcKeywordVolumeModel.keyword_normalized == RrugcKeywordAgentOriginModel.keyword_normalized)
        ))
        .where(RrugcKeywordAgentOriginModel.agent_name == AGENT_NAME)
        .order_by(RrugcKeywordVolumeModel.created_at.asc(), RrugcKeywordAgentOriginModel.first_seen_at.asc())
        .limit(3000)
    ).all()
    images, metrics = [], []
    seen_image, seen_metrics = set(), set()
    for row, evidence in rows:
        key = (row.tenant_id, row.id)
        if (len(images) < image_limit and key not in seen_image and not row.source_image_url
                and due(row.provider_raw_json, ATTEMPT_IMAGE_KEY, now, IMAGE_RETRY_AFTER)):
            source = checked_marketplace_source(evidence.source_platform, evidence.source_url or "")
            if source:
                images.append((row.tenant_id, row.id, evidence.source_platform, source))
                seen_image.add(key)
        if (len(metrics) < metrics_limit and key not in seen_metrics
                and row.provider == KEYWORD_VOLUME_PENDING_PROVIDER
                and due(row.provider_raw_json, ATTEMPT_METRICS_KEY, now, METRICS_RETRY_AFTER)):
            metrics.append((row.tenant_id, row.id, row.keyword))
            seen_metrics.add(key)
    return images, metrics


def claim_attempt(session: Session, *, tenant: str, row_id: str, key: str, now: datetime) -> bool:
    row = session.scalar(select(RrugcKeywordVolumeModel).where(
        RrugcKeywordVolumeModel.id == row_id,
        RrugcKeywordVolumeModel.tenant_id == tenant,
    ).with_for_update())
    if not row or not due(row.provider_raw_json, key, now, IMAGE_RETRY_AFTER if key == ATTEMPT_IMAGE_KEY else METRICS_RETRY_AFTER):
        return False
    if key == ATTEMPT_IMAGE_KEY and row.source_image_url:
        return False
    if key == ATTEMPT_METRICS_KEY and row.provider != KEYWORD_VOLUME_PENDING_PROVIDER:
        return False
    row.provider_raw_json = {**(row.provider_raw_json or {}), key: now.isoformat()}
    session.commit()
    return True


async def enrich_image(tenant: str, row_id: str, platform: str, source: str,
                       *, client: httpx.AsyncClient, now: datetime) -> bool:
    with SessionLocal() as session:
        if not claim_attempt(session, tenant=tenant, row_id=row_id, key=ATTEMPT_IMAGE_KEY, now=now):
            return False
    try:
        page = await fetch_product_page(client, source)
        choices = select_image_url(platform, page.images)
        # Do not accept an unrelated site logo or tracking pixel.
        for candidate in choices:
            try:
                data, content_type = await fetch_product_image(client, candidate)
                if content_type not in {"image/jpeg", "image/png", "image/webp", "image/avif"} or len(data) < 2500:
                    continue
                if not (data.startswith(bytes.fromhex("ffd8ff")) or data.startswith(bytes.fromhex("89504e47")) or
                        data.startswith(b"RIFF") or data[4:12] in (b"ftypavif", b"ftypavis")):
                    continue
                with SessionLocal() as session:
                    row = session.scalar(select(RrugcKeywordVolumeModel).where(
                        RrugcKeywordVolumeModel.tenant_id == tenant,
                        RrugcKeywordVolumeModel.id == row_id,
                    ).with_for_update())
                    if not row or row.source_image_url:
                        return False
                    row.source_image_url = candidate
                    row.provider_raw_json = {**(row.provider_raw_json or {}), IMAGE_STATE_KEY: "verified"}
                    # Preserve attribution and existing AEBrowse metrics.
                    session.commit()
                return True
            except (ProductPageImportError, httpx.HTTPError):
                continue
    except ProductPageImportError as exc:
        status = "source_blocked" if exc.code in {"product_source_fetch_failed", "product_source_unavailable"} else "metadata_unavailable"
    except (httpx.HTTPError, ValueError):
        status = "source_unavailable"
    else:
        status = "image_unavailable"
    with SessionLocal() as session:
        row = session.scalar(select(RrugcKeywordVolumeModel).where(
            RrugcKeywordVolumeModel.tenant_id == tenant,
            RrugcKeywordVolumeModel.id == row_id,
        ).with_for_update())
        if row and not row.source_image_url:
            row.provider_raw_json = {**(row.provider_raw_json or {}), IMAGE_STATE_KEY: status}
            session.commit()
    return False


async def run(*, images: int = 4, metrics: int = 6) -> dict:
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        image_jobs, metric_jobs = pending_rows(session, image_limit=images, metrics_limit=metrics, now=now)
    result = {"images_attempted": 0, "images_found": 0, "metrics_attempted": 0, "metrics_resolved": 0,
              "metrics_failed": 0}
    async with httpx.AsyncClient(timeout=httpx.Timeout(14.0, connect=5.0),
                                 follow_redirects=False, trust_env=False,
                                 headers={"User-Agent": "CreativeAssetManager/Stage0PublicListingPreview/1.0"}) as client:
        for tenant, row_id, platform, source in image_jobs:
            result["images_attempted"] += 1
            result["images_found"] += int(await enrich_image(tenant, row_id, platform, source, client=client, now=now))
    # One AEBrowse batch per tenant; cap total input, do not hold a DB
    # connection during product page fetching. The provider service owns
    # its own retry/backoff and commits only real matching measurements.
    by_tenant: dict[str, list[tuple[str, str]]] = {}
    for tenant, row_id, keyword in metric_jobs:
        by_tenant.setdefault(tenant, []).append((row_id, keyword))
    for tenant, entries in by_tenant.items():
        with SessionLocal() as session:
            claimable = [keyword for row_id, keyword in entries if claim_attempt(
                session, tenant=tenant, row_id=row_id, key=ATTEMPT_METRICS_KEY, now=now)]
        if not claimable:
            continue
        result["metrics_attempted"] += len(claimable)
        try:
            with SessionLocal() as session:
                await RrugcKeywordVolumeService(session).resolve(tenant_id=tenant, keywords=claimable)
                result["metrics_resolved"] += sum(
                    1 for row in session.scalars(select(RrugcKeywordVolumeModel).where(
                        RrugcKeywordVolumeModel.tenant_id == tenant,
                        RrugcKeywordVolumeModel.keyword.in_(claimable),
                        RrugcKeywordVolumeModel.provider != KEYWORD_VOLUME_PENDING_PROVIDER,
                    )).all()
                )
        except (KeywordVolumeError, httpx.HTTPError) as exc:
            result["metrics_failed"] += len(claimable)
            LOGGER.warning("stage0_aebrowse_unavailable error_type=%s", type(exc).__name__)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", type=int, default=4)
    parser.add_argument("--metrics", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not (0 <= args.images <= 10 and 0 <= args.metrics <= 20):
        parser.error("limits out of range")
    if args.dry_run:
        with SessionLocal() as session:
            images, metrics = pending_rows(session, image_limit=args.images,
                                           metrics_limit=args.metrics, now=datetime.now(timezone.utc))
        print(json.dumps({"mode": "dry-run", "image_candidates": len(images),
                          "metrics_candidates": len(metrics)}, sort_keys=True))
        return 0
    print(json.dumps({"mode": "apply", **asyncio.run(run(images=args.images, metrics=args.metrics))},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
