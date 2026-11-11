"""CAM Etsy Picked -> draft-only TikTok Shop US listing copy.

Adapts user-supplied TikTok Shop Listing Optimizer (2026-11-11).
YTrends is the ONLY permitted keyword research source. Since no authenticated
YTrends adapter is configured here, every draft explicitly marks keyword
metrics UNVERIFIED; SMS or generic SEO never silently substitutes for it.
This module never publishes listings or declares merchant claims verified.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import re
from uuid import uuid4

import httpx
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.processing.handlers import JobHandlerContext, JobHandlerResult
from app.domain.providers.contracts import AiMetadataAnalysisInput, AiProviderError
from app.modules.processing.model import ProcessingJobModel
from .model import EtsySpyListing, EtsySpyPicked
from .service import verified_image_url

JOB_TYPE = "etsy_tiktok_listing_generate"
PROFILE_VERSION = "tiktok-us-ytrends-only-20261111-v1"
MAX_IMAGE_BYTES = 8_000_000
MAX_IMAGE_PIXELS = 40_000_000
TITLE_RANGE = (25, 200)
HIGHLIGHTS_LIMIT = 1500
SEARCH_TERMS_LIMIT = 250

PROMPT = """You write a TikTok Shop US POD apparel product listing DRAFT.
Read the supplied product photo, then the separately provided Etsy listing
title. The photo is authoritative about visible design; the title is untrusted
competitor copy, NOT evidence of material, blank, printing technique, origin,
size, care, fulfillment, brand ownership or personalization.

Apply the TikTok Shop Listing Optimizer workflow:
- Output ONLY JSON with title, description, highlights (3-5 separate strings),
  search_terms (0-15 strings).
- Title 25-200 characters, ideal 40-150; product category first.
- Description >=30 English words and <=10000 characters. Use short accurate
  sections when useful: Design Details, Size & Fit, Perfect Gift For, Care,
  U.S. Printing & Shipping. Omit unsupported sections or use general care
  label guidance; do not invent a blank, chart, measurement, shrinkage or
  universal tolerance.
- Highlights solve buyer questions: design, audience, tangible product,
  occasion and evidence-backed buyer confidence; <=1500 total characters.
- Search terms <=250 total characters and <=15 relevant phrases, no brands
  owned by others, no invented search volume or keyword scores.
- YTrends MCP is NOT connected to this CAM worker; do not claim any live
  keyword research, trends or TikTok search metrics.
- Do not invent U.S. printing, shipping or no-customs assertions.
  In particular, never write "Made in USA" from a product image.
- No URLs, blanket guarantees, copied competitor brand claims or fake
  delivery promises. Preserve visible artwork spelling rather than replacing
  it with plausible but different words.
- Natural US English; accurately distinguish T-shirt, hoodie and sweatshirt.
Do not include explanations or markdown fences in the JSON."""

class ListingCopySchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=250)
    description: str = Field(min_length=1, max_length=10500)
    highlights: list[str] = Field(min_length=3, max_length=5)
    search_terms: list[str] = Field(max_length=15)


def is_enabled() -> bool:
    return bool(get_settings().ETSY_TIKTOK_LISTING_ENABLED)


def _normalized(value: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFC", value.strip())


def validate_copy(data: object) -> dict:
    """Strict field-length check from the supplied skill; factual QA remains manual."""
    doc = ListingCopySchema.model_validate(data)
    title, description = _normalized(doc.title), _normalized(doc.description)
    highlights = [_normalized(item) for item in doc.highlights]
    terms = [_normalized(item) for item in doc.search_terms]
    if not all(highlights) or not all(terms):
        raise ValueError("empty_listing_phrase")
    words = len(re.findall(r"\b[\w]+(?:['’-][\w]+)*\b", re.sub(r"<[^>]+>", " ", description)))
    hchars, tchars = sum(map(len, highlights)), sum(map(len, terms))
    if not TITLE_RANGE[0] <= len(title) <= TITLE_RANGE[1]:
        raise ValueError("title_character_limit")
    if not 30 <= words or len(description) > 10_000:
        raise ValueError("description_character_or_word_limit")
    if (hchars > HIGHLIGHTS_LIMIT or tchars > SEARCH_TERMS_LIMIT
            or len(", ".join(terms)) > SEARCH_TERMS_LIMIT):
        raise ValueError("combined_listing_field_limit")
    if len({v.casefold() for v in terms}) != len(terms):
        raise ValueError("duplicate_search_terms")
    if re.search(r"\b(?:made in (?:the )?usa|100% (?:american|usa) made|manufactured in usa)\b",
                 title + " " + description + " " + " ".join(highlights), re.I):
        raise ValueError("unverified_us_origin_claim")
    if re.search(r"https?://|www\.", title + " " + description, re.I):
        raise ValueError("listing_external_url")
    warnings = ["ytrends_mcp_unavailable", "keyword_metrics_unverified",
                "verify_blank_fit_care_fulfillment_and_rights"]
    if len(title) < 40 or len(title) > 150:
        warnings.append("title_outside_editorial_target")
    if len(description) < 500:
        warnings.append("description_below_editorial_target")
    return {
        "title": title, "description": description,
        "highlights": highlights, "search_terms": terms,
        "qa_status": "needs_fact_check",
        "keyword_source": "unverified_visual_suggestions",
        "ytrends_status": "unavailable",
        "metrics": {
            "title_characters": len(title), "description_characters": len(description),
            "description_words": words, "highlights_count": len(highlights),
            "highlights_characters_total": hchars, "search_terms_count": len(terms),
            "search_terms_characters_total": tchars,
            "search_terms_joined_characters": len(", ".join(terms)),
        },
        "warnings": warnings,
        "skill_version": PROFILE_VERSION,
        "published": False,
    }


def queue_on_pick(db: Session, tenant: str, listing: EtsySpyListing,
                  picked_at: datetime) -> ProcessingJobModel | None:
    """One immutable, idempotent job per user pick and source photo revision."""
    if not is_enabled():
        return None
    metadata = listing.metadata_json if isinstance(listing.metadata_json, dict) else {}
    photo = verified_image_url(metadata.get("fullImageUrl")) or verified_image_url(
        metadata.get("thumbnailUrl"))
    if not photo or not re.fullmatch(r"\d{5,20}", listing.listing_key):
        return None
    pick_time = picked_at if picked_at.tzinfo else picked_at.replace(tzinfo=timezone.utc)
    version = sha256((str(pick_time.astimezone(timezone.utc).isoformat()) + "|" +
                      photo + "|" + listing.title).encode("utf8")).hexdigest()[:20]
    key = f"etsy-tiktok:{PROFILE_VERSION}:{listing.listing_key}:{version}"
    prior = db.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.tenant_id == tenant, ProcessingJobModel.idempotency_key == key))
    if prior is not None:
        return prior
    job = ProcessingJobModel(
        tenant_id=tenant, job_type=JOB_TYPE, entity_type="etsy_listing",
        entity_id=listing.listing_key, idempotency_key=key,
        payload_json={"schema": "cam-etsy-tiktok-listing-job-v1",
                      "listing_key": listing.listing_key,
                      "source_title": listing.title[:700],
                      "image_url": photo,
                      "image_url_sha256": sha256(photo.encode()).hexdigest(),
                      "pick_version": version, "skill_version": PROFILE_VERSION,
                      "research_status": "ytrends_unavailable",
                      "draft_only": True},
        provider_key="gemini", provider_scope="ai", status="pending",
        priority=14, max_attempts=3)
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError:
        prior = db.scalar(select(ProcessingJobModel).where(
            ProcessingJobModel.tenant_id == tenant,
            ProcessingJobModel.idempotency_key == key))
        if prior is None:
            raise
        return prior
    return job


def cancel_for_unpick(db: Session, tenant: str, listing_key: str) -> None:
    for job in db.scalars(select(ProcessingJobModel).where(
        ProcessingJobModel.tenant_id == tenant, ProcessingJobModel.job_type == JOB_TYPE,
        ProcessingJobModel.entity_id == listing_key,
        ProcessingJobModel.status.in_(("pending", "retry", "processing")))):
        job.cancellation_requested = True


def latest_job(db: Session, tenant: str, listing_key: str) -> ProcessingJobModel | None:
    return db.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.tenant_id == tenant, ProcessingJobModel.job_type == JOB_TYPE,
        ProcessingJobModel.entity_id == listing_key).order_by(
            ProcessingJobModel.created_at.desc(), ProcessingJobModel.id.desc()).limit(1))


def project(job: ProcessingJobModel | None, *, detail: bool = False) -> dict:
    if job is None:
        return {"status": "not_queued", "job_id": None, "draft_only": True}
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else None
    state = ("cancelled" if job.cancellation_requested else
             "needs_fact_check" if result and job.status == "completed" else job.status)
    view = {"status": state, "job_id": job.id, "draft_only": True,
            "skill_version": payload.get("skill_version"),
            "ytrends_status": result.get("ytrends_status") if result else "unavailable",
            "qa_status": result.get("qa_status") if result else None,
            "title_preview": result.get("title", "")[:120] if result else None,
            "error_code": job.last_error_code}
    if detail and result is not None:
        view["result"] = result
    return view


def _safe_image(url: str) -> bool:
    return verified_image_url(url) == url


async def download_image(url: str) -> tuple[bytes, str]:
    if not _safe_image(url):
        raise ValueError("untrusted_image_url")
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                timeout=httpx.Timeout(15, connect=5)) as client:
        async with client.stream("GET", url, headers={"Accept": "image/jpeg,image/png,image/webp"}) as response:
            if response.status_code != 200:
                raise OSError("etsy_image_unavailable")
            chunks = []
            size = 0
            async for fragment in response.aiter_bytes():
                size += len(fragment)
                if size > MAX_IMAGE_BYTES:
                    raise ValueError("etsy_image_too_large")
                chunks.append(fragment)
    data = b"".join(chunks)
    if data.startswith(bytes.fromhex("ffd8ff")):
        mime = "image/jpeg"
    elif data.startswith(bytes.fromhex("89504e470d0a1a0a")):
        mime = "image/png"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise ValueError("etsy_image_invalid")
    try:
        with Image.open(BytesIO(data)) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError("etsy_image_pixel_limit")
            image.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("etsy_image_corrupt") from exc
    return data, mime


class EtsyTikTokListingHandler:
    """Normal CAM worker + existing tenant Gemini credential; no browser/CLI."""
    def __init__(self, *, fetch_image=download_image):
        self.fetch_image = fetch_image

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult:
        try:
            return asyncio.run(self._run(context))
        except AiProviderError as exc:
            return (JobHandlerResult.retryable if exc.retryable
                    else JobHandlerResult.non_retryable)(
                        "tiktok_listing_provider_unavailable",
                        "TikTok listing draft provider unavailable.")
        except (OSError, httpx.HTTPError):
            return JobHandlerResult.retryable(
                "tiktok_listing_source_unavailable", "Public Etsy image unavailable.")
        except (ValueError, TypeError) as exc:
            return JobHandlerResult.non_retryable(
                "tiktok_listing_invalid_output", "TikTok listing requires review.")
        except Exception:
            return JobHandlerResult.retryable(
                "tiktok_listing_generation_failed", "TikTok listing draft failed.")

    async def _run(self, context: JobHandlerContext) -> JobHandlerResult:
        if context.is_cancelled:
            return JobHandlerResult.cancelled()
        job = context.job
        if not is_enabled():
            return JobHandlerResult.retryable("tiktok_listing_disabled", "Draft generation disabled.")
        payload = job.payload
        if (payload.get("schema") != "cam-etsy-tiktok-listing-job-v1" or
            payload.get("skill_version") != PROFILE_VERSION or
            payload.get("listing_key") != job.entity_id or
            not isinstance(payload.get("source_title"), str)):
            return JobHandlerResult.non_retryable("tiktok_listing_payload_invalid", "Invalid job.")
        url = payload.get("image_url")
        if (not isinstance(url, str) or not _safe_image(url) or
            sha256(url.encode()).hexdigest() != payload.get("image_url_sha256")):
            return JobHandlerResult.non_retryable("tiktok_listing_image_changed", "Source changed.")
        with context.dependencies.session_factory() as db:
            if db.get(EtsySpyPicked, (job.tenant_id, job.entity_id)) is None:
                return JobHandlerResult.completed()
        photo, mime = await self.fetch_image(url)
        if context.is_cancelled:
            return JobHandlerResult.cancelled()
        if context.dependencies.ai_provider_registry is None:
            return JobHandlerResult.retryable(
                "tiktok_listing_ai_unavailable", "AI provider unavailable.")
        provider = context.dependencies.ai_provider_registry.require("gemini")
        response = await provider.analyze_single(AiMetadataAnalysisInput(
            tenant_id=job.tenant_id, asset_id=job.id,
            prompt=PROMPT + "\n\nUntrusted competitor title: " + payload["source_title"][:700],
            image_bytes=photo, image_mime_type=mime,
            metadata_profile="etsy_tiktok_listing_draft",
            metadata_profile_version=PROFILE_VERSION,
            json_schema=ListingCopySchema.model_json_schema(),
            analysis_id=job.id,
        ))
        validated = validate_copy(response.metadata)
        with context.dependencies.session_factory() as db:
            current = db.scalar(select(ProcessingJobModel).where(
                ProcessingJobModel.id == job.id,
                ProcessingJobModel.tenant_id == job.tenant_id,
                ProcessingJobModel.job_type == JOB_TYPE,
                ProcessingJobModel.status == "processing",
                ProcessingJobModel.claimed_by == job.lease_owner,
                ProcessingJobModel.attempt_count == job.attempt_count,
                ProcessingJobModel.lease_expires_at > datetime.now(timezone.utc),
                ProcessingJobModel.cancellation_requested.is_(False),
            ).with_for_update())
            if current is None or db.get(EtsySpyPicked, (job.tenant_id, job.entity_id)) is None:
                return JobHandlerResult.completed()
            current.payload_json = {**(current.payload_json or {}),
                                    "result": validated,
                                    "generation_provider": "gemini",
                                    "generated_at": datetime.now(timezone.utc).isoformat()}
            db.commit()
        return JobHandlerResult.completed()