"""Low-priority verified-image review of public Agent-Reach marketplace listings.

One durable processing job per signal. Only a real, public, canonical listing
and a bounded allowlisted CDN image are passed to the existing RRUGC Gemini
pool. Any uncertainty leaves the research signal pending for human review.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from html.parser import HTMLParser
import logging
import re
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.domain.processing.handlers import DeferredJobOutcome, JobHandlerContext, JobHandlerResult
from app.domain.providers.contracts import AiMetadataAnalysisInput, AiProviderError
from app.domain.providers.registry import AiProviderUnavailableError
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.realistic_review_ugc.gemini_safety import deferred_rrugc_ai_retry, scheduled_rrugc_gemini_slot
from app.modules.realistic_review_ugc.model import RrugcResearchSignalModel, RrugcScoutAgentModel
from app.modules.realistic_review_ugc.research_discovery import AGENT_NAME
from app.modules.realistic_review_ugc.research_intelligence import (
    _insert_stage0_pending_keyword, canonical_marketplace_url,
)
from app.modules.realistic_review_ugc.research_keyword_extraction import _valid

LOGGER = logging.getLogger("cam.research.vision")
JOB_TYPE = "rrugc_research_vision"
VISION_VERSION = "agent-reach-verified-vision-v1"
MAX_ACTIVE = 4
MAX_PER_TICK = 4
MAX_HTML_BYTES = 1_000_000
MAX_IMAGE_BYTES = 8_000_000
IMAGE_HOSTS = {
    "etsy": frozenset({"i.etsystatic.com", "img.etsystatic.com"}),
    "amazon": frozenset({"m.media-amazon.com", "images-na.ssl-images-amazon.com", "images-eu.ssl-images-amazon.com"}),
    "pinterest": frozenset({"i.pinimg.com"}),
}
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


class PublicImageUnavailable(Exception):
    """Unavailable public evidence is not a claim that a quote is invalid."""


class ImageMetadataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta":
            return
        props = {str(k).lower(): str(v or "") for k, v in attrs}
        if props.get("property", "").lower() in {"og:image", "og:image:secure_url"} or props.get("name", "").lower() in {"twitter:image"}:
            if props.get("content"):
                self.images.append(props["content"])


def permitted_image_url(url: str, source: str) -> bool:
    try:
        parts = urlsplit(url)
        return (
            parts.scheme == "https"
            and not parts.username and not parts.password
            and parts.port in (None, 443)
            and parts.hostname in IMAGE_HOSTS.get(source, ())
            and len(url) <= 2048
            and not parts.fragment
        )
    except ValueError:
        return False


async def fetch_public_listing_image(url: str, source: str) -> tuple[bytes, str, str]:
    """Do not follow redirects, fetch search-result supplied URLs or proxy a LAN host."""
    from app.modules.realistic_review_ugc.research_intelligence import ResearchValidationError
    try:
        valid_url = source in IMAGE_HOSTS and canonical_marketplace_url(source, url) == url
    except ResearchValidationError:
        valid_url = False
    if not valid_url:
        raise PublicImageUnavailable("invalid_canonical_listing")
    async with httpx.AsyncClient(timeout=httpx.Timeout(12, connect=4), follow_redirects=False,
                                trust_env=False) as client:
        try:
            async with client.stream("GET", url, headers={"User-Agent": "CAM-ResearchImageReview/1.0",
                "Accept": "text/html"}) as response:
                if response.status_code != 200 or "text/html" not in response.headers.get("content-type", "").lower():
                    raise PublicImageUnavailable("listing_not_public")
                chunks = []
                length = 0
                async for block in response.aiter_bytes():
                    length += len(block)
                    if length > MAX_HTML_BYTES:
                        raise PublicImageUnavailable("listing_html_too_large")
                    chunks.append(block)
            parser = ImageMetadataParser()
            parser.feed(b"".join(chunks).decode("utf-8", errors="replace"))
            image_url = next((v for v in parser.images if permitted_image_url(v, source)), "")
            if not image_url:
                raise PublicImageUnavailable("listing_image_unavailable")
            async with client.stream("GET", image_url, headers={"Accept": "image/avif,image/webp,image/png,image/jpeg"}) as response:
                mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if response.status_code != 200 or mime not in IMAGE_TYPES:
                    raise PublicImageUnavailable("listing_image_not_available")
                image_chunks = []
                total = 0
                async for block in response.aiter_bytes():
                    total += len(block)
                    if total > MAX_IMAGE_BYTES:
                        raise PublicImageUnavailable("listing_image_too_large")
                    image_chunks.append(block)
            data = b"".join(image_chunks)
            if len(data) < 256 or not (
                data.startswith(b"\\xff\\xd8\\xff") or data.startswith(b"\\x89PNG\\r\\n\\x1a\\n")
                or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
            ):
                raise PublicImageUnavailable("listing_image_invalid")
            return data, mime, image_url
        except httpx.RequestError as exc:
            raise PublicImageUnavailable("public_source_network_unavailable") from exc


class ResearchCapVision(BaseModel):
    model_config = ConfigDict(extra="ignore")
    is_baseball_cap: bool
    text_physically_on_cap: bool
    quote: str = Field(max_length=150)
    confidence: float = Field(ge=0.0, le=1.0)
    multiple_caps_or_unclear_target: bool
    shows_brand_logo_or_character: bool
    visual_evidence: str = Field(max_length=300)


def verified_quote(document: ResearchCapVision, title: str) -> str | None:
    """Fail closed on uncertain OCR, product copy and a phrase absent from title."""
    if not (document.is_baseball_cap and document.text_physically_on_cap
            and not document.multiple_caps_or_unclear_target
            and not document.shows_brand_logo_or_character
            and document.confidence >= 0.94):
        return None
    phrase = _valid(document.quote)
    if not phrase:
        return None
    # Independent agreement with the public listing title reduces hallucinated OCR.
    normalize = lambda value: " ".join(re.findall(r"[A-Z0-9]+", value.upper()))
    if normalize(phrase) not in normalize(title):
        return None
    return phrase


PROMPT = """Read ONLY lettering physically visible on the cap in this marketplace PRODUCT IMAGE.
Ignore any text elsewhere in the photo, metadata and listing titles. Do not guess
words obscured by folds or invented by image-generation artifacts. The cap must
be a real baseball/trucker/dad/snapback cap with a brim. If multiple caps have
different texts, mark multiple_caps_or_unclear_target true.
Output the exact lettering as quote, preserving word order; use an empty
string if partly unreadable. Confidence must reflect actual legibility (0–1).
Set shows_brand_logo_or_character true for obvious logos, brands, famous
characters or team insignia; this is only a visual screening, NOT trademark
clearance. Never classify a product description as physical embroidery.
Return JSON matching the supplied schema."""


def enqueue_research_vision(session, tenant_id: str, *, limit: int = MAX_PER_TICK) -> int:
    agent_ids = list(session.scalars(select(RrugcScoutAgentModel.id).where(
        RrugcScoutAgentModel.tenant_id == tenant_id,
        RrugcScoutAgentModel.name == AGENT_NAME,
        RrugcScoutAgentModel.active.is_(True),
    )))
    if not agent_ids:
        return 0
    from sqlalchemy import inspect
    if not inspect(session.get_bind()).has_table("processing_jobs"):
        return 0
    active = int(session.scalar(select(func.count(ProcessingJobModel.id)).where(
        ProcessingJobModel.tenant_id == tenant_id,
        ProcessingJobModel.job_type == JOB_TYPE,
        ProcessingJobModel.status.in_(("pending", "retry", "processing")),
    )) or 0)
    remaining = max(0, min(MAX_PER_TICK, limit, MAX_ACTIVE - active))
    if not remaining:
        return 0
    candidates = session.scalars(select(RrugcResearchSignalModel).where(
        RrugcResearchSignalModel.tenant_id == tenant_id,
        RrugcResearchSignalModel.channel == "auto-discover",
        RrugcResearchSignalModel.status == "pending",
        RrugcResearchSignalModel.source.in_(tuple(IMAGE_HOSTS)),
        RrugcResearchSignalModel.created_by_user_id.in_(["scout-agent:" + x for x in agent_ids]),
    ).order_by(RrugcResearchSignalModel.created_at.asc()).limit(500)).all()
    repo = ProcessingRepository(session)
    created = 0
    for row in candidates:
        _, added = repo.create_job_once(
            tenant_id=tenant_id, job_type=JOB_TYPE,
            entity_type="rrugc_research_signal", entity_id=row.id,
            idempotency_key=f"rrugc:research:vision:{VISION_VERSION}:{row.id}",
            payload={"signal_id": row.id, "vision_version": VISION_VERSION},
            provider_key="gemini", provider_scope="ai", priority=2,
            max_attempts=4,
        )
        if added:
            created += 1
        if created >= remaining:
            break
    session.commit()
    return created


def record_vision_evidence(context: JobHandlerContext, **fields: str | float) -> None:
    with context.dependencies.session_factory() as session:
        job = session.get(ProcessingJobModel, context.job.id)
        if job is not None:
            job.payload = {**(job.payload or {}), **fields}
            session.commit()


class ResearchVisionJobHandler:
    def __call__(self, context: JobHandlerContext) -> JobHandlerResult | DeferredJobOutcome:
        try:
            return asyncio.run(self._run(context))
        except PublicImageUnavailable as exc:
            record_vision_evidence(context, vision_reason=str(exc))
            context.logger.info("research_vision_source_unavailable", extra={
                "signal_id": context.job.entity_id, "reason": str(exc)})
            # Never reject on 403, missing photos or third-party access failure.
            return JobHandlerResult.completed()
        except AiProviderError as exc:
            deferred = deferred_rrugc_ai_retry(exc, message="Research Gemini shared pool busy.")
            if deferred is not None:
                return deferred
            return (JobHandlerResult.retryable if exc.retryable else JobHandlerResult.non_retryable)(
                exc.code, "Research visual verification deferred.")
        except AiProviderUnavailableError:
            return JobHandlerResult.retryable("gemini_provider_unavailable", "Gemini is unavailable.")
        except Exception:
            context.logger.exception("research_vision_job_error", extra={"signal_id": context.job.entity_id})
            return JobHandlerResult.retryable("research_vision_failed", "Research visual review failed.")

    async def _run(self, context: JobHandlerContext) -> JobHandlerResult:
        tenant_id, signal_id = context.job.tenant_id, context.job.entity_id
        if context.job.payload.get("signal_id") != signal_id:
            return JobHandlerResult.non_retryable("research_vision_job_invalid", "Invalid signal identifier.")
        with context.dependencies.session_factory() as session:
            row = session.scalar(select(RrugcResearchSignalModel).where(
                RrugcResearchSignalModel.id == signal_id,
                RrugcResearchSignalModel.tenant_id == tenant_id,
                RrugcResearchSignalModel.status == "pending",
                RrugcResearchSignalModel.channel == "auto-discover",
            ))
            if row is None:
                return JobHandlerResult.completed()
            url, title, source = row.url, row.title, row.source
        image_bytes, mime, image_url = await fetch_public_listing_image(url, source)
        record_vision_evidence(context, vision_image_url=image_url, vision_reason="gemini_pending")
        if context.is_cancelled:
            return JobHandlerResult.cancelled()
        registry = context.dependencies.ai_provider_registry
        if registry is None:
            raise AiProviderUnavailableError("gemini")
        provider = registry.require("gemini")
        slot = scheduled_rrugc_gemini_slot(context.job)
        analysis = await provider.analyze_single(AiMetadataAnalysisInput(
            tenant_id=tenant_id, asset_id=signal_id, prompt=PROMPT,
            image_bytes=image_bytes, image_mime_type=mime,
            metadata_profile="rrugc_research_listing_vision",
            metadata_profile_version=VISION_VERSION,
            json_schema=ResearchCapVision.model_json_schema(),
            analysis_id=signal_id,
            preferred_model=slot.model if slot else None,
            preferred_credential_provider=slot.credential_provider if slot else None,
        ))
        doc = ResearchCapVision.model_validate(dict(analysis.metadata))
        quote = verified_quote(doc, title)
        record_vision_evidence(
            context, vision_confidence=doc.confidence,
            vision_reason="verified_quote" if quote else "needs_human_review",
        )
        with context.dependencies.session_factory() as session:
            row = session.scalar(select(RrugcResearchSignalModel).where(
                RrugcResearchSignalModel.tenant_id == tenant_id,
                RrugcResearchSignalModel.id == signal_id,
            ).with_for_update())
            if row is None or row.status != "pending":
                return JobHandlerResult.completed()
            if not quote:
                context.logger.info("research_vision_needs_review", extra={
                    "signal_id": signal_id, "confidence": doc.confidence,
                    "has_visible_quote": doc.text_physically_on_cap})
                return JobHandlerResult.completed()
            row.keyword = quote
            row.reviewed_by_user_id = "auto:agent-reach:" + VISION_VERSION
            row.reviewed_at = datetime.now(timezone.utc)
            _insert_stage0_pending_keyword(session, tenant_id, row)
            row.status = "approved"
            session.commit()
            context.logger.info("research_vision_auto_approved", extra={
                "signal_id": signal_id, "source": source,
                "image_host": urlsplit(image_url).hostname})
        return JobHandlerResult.completed()
