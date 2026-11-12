"""CAM Etsy Picked -> draft-only TikTok Shop US listing copy.

Adapts user-supplied TikTok Shop Listing Optimizer (2026-11-11).
YTrends is the only external Etsy market keyword context. Its snapshots are
not TikTok Shop keyword evidence; all TikTok search metrics remain unverified.
This module never publishes listings or declares merchant claims verified.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import re
from uuid import uuid4

import httpx
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.processing.handlers import JobHandlerContext, JobHandlerResult, DeferredJobOutcome
from app.domain.providers.contracts import AiMetadataAnalysisInput, AiProviderError
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.gemini_safety import (
    scheduled_rrugc_gemini_slot, deferred_rrugc_ai_retry,
)
from .model import EtsySpyListing, EtsySpyPicked
from .service import verified_image_url
from .ytrends_mcp import empty_evidence, prompt_context, research_etsy_keyword

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
- Description MUST contain at least 60 natural English words (aim 70-110)
  while staying <=10000 characters. Use short accurate sections when useful:
  Design Details, Size & Fit, Perfect Gift For, Care. Omit unsupported sections
  or use general care-label guidance; do not invent a blank, chart,
  measurement, shrinkage or universal tolerance. Do not pad with invented facts.
- Highlights solve buyer questions: design, audience, tangible product,
  occasion and evidence-backed buyer confidence; <=1500 total characters.
- Search terms <=250 total characters and <=15 relevant phrases, no brands
  owned by others, no invented search volume or keyword scores.
- The separate YTrends context, if present, represents an Etsy indexed subset
  with dated snapshots, NOT TikTok Shop keyword demand or rank. Never invent
  TikTok search volumes, ranking metrics or unsupported trending claims.
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


def auto_on_pick_enabled() -> bool:
    settings = get_settings()
    return bool(settings.ETSY_TIKTOK_LISTING_ENABLED and
                settings.ETSY_TIKTOK_AUTO_ON_PICK_ENABLED)


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
    warnings = ["tiktok_keyword_metrics_unverified",
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


class GeneratedListingInvalid(ValueError):
    """Only model-generated copy errors qualify for bounded automatic retry."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def prepare_generated_copy(raw: object) -> dict:
    """Repair formatting only; never synthesize garment, rights or shipping facts.

    Gemini structured JSON occasionally contains harmless extra keys, aliases,
    duplicate keywords, or an overlong optional keyword list. Canonicalizing
    these is cheaper and safer than paying for a new model attempt. Structural
    omissions and substantive copy failures still go through a fresh attempt.
    """
    if not isinstance(raw, Mapping):
        raise GeneratedListingInvalid("root_not_object")
    if not any(key in raw for key in ("title", "product_title")):
        for wrapper in ("listing", "result", "product_listing"):
            nested = raw.get(wrapper)
            if isinstance(nested, Mapping):
                raw = nested
                break
    aliases = {
        "title": ("title", "product_title"),
        "description": ("description", "product_description"),
        "highlights": ("highlights", "product_highlights"),
        "search_terms": ("search_terms", "searchTerms", "keywords"),
    }
    fields = {}
    for name, keys in aliases.items():
        fields[name] = next((raw[key] for key in keys if key in raw), None)
    if not isinstance(fields["title"], str):
        raise GeneratedListingInvalid("missing_title")
    if not isinstance(fields["description"], str):
        raise GeneratedListingInvalid("missing_description")
    highlights = fields["highlights"]
    if not isinstance(highlights, list) or not all(isinstance(x, str) for x in highlights):
        raise GeneratedListingInvalid("invalid_highlights")
    # Retain the first five real highlights. Never invent missing highlights.
    highlights = [_normalized(x) for x in highlights if x.strip()][:5]
    terms = fields["search_terms"]
    if terms is None:
        terms = []  # Search terms are optional while YTrends remains unavailable.
    if isinstance(terms, str):
        terms = re.split(r"[,;\n]", terms)
    if not isinstance(terms, list) or not all(isinstance(x, str) for x in terms):
        raise GeneratedListingInvalid("invalid_search_terms")
    terms_clean = []
    seen = set()
    for term in terms:
        normalized = _normalized(term)
        if not normalized or normalized.casefold() in seen:
            continue
        if len(terms_clean) >= 15:
            break
        candidate = terms_clean + [normalized]
        if (sum(map(len, candidate)) > SEARCH_TERMS_LIMIT or
                len(", ".join(candidate)) > SEARCH_TERMS_LIMIT):
            # Discard this optional phrase, retaining any shorter later ones.
            continue
        seen.add(normalized.casefold())
        terms_clean.append(normalized)
    return {
        "title": _normalized(fields["title"]),
        "description": _normalized(fields["description"]),
        "highlights": highlights,
        "search_terms": terms_clean,
    }


def _safe_validation_reason(exc: Exception) -> str:
    """No model text, prompt, provider secret or raw validation error in logs."""
    if isinstance(exc, GeneratedListingInvalid):
        return exc.reason
    if isinstance(exc, ValidationError):
        first = exc.errors(include_input=False)[0]
        field = str((first.get("loc") or ("root",))[0])
        kind = str(first.get("type") or "invalid")
        if field in ("title", "description", "highlights", "search_terms"):
            if kind in ("missing", "list_type", "string_type", "too_short",
                        "too_long", "string_too_short", "string_too_long"):
                return f"schema_{field}_{kind}"
        return "schema_validation_failed"
    code = str(exc)
    allowed = {
        "empty_listing_phrase", "title_character_limit",
        "description_character_or_word_limit", "combined_listing_field_limit",
        "duplicate_search_terms", "unverified_us_origin_claim",
        "listing_external_url",
    }
    return code if code in allowed else "schema_validation_failed"


def _retry_instruction(attempt: int) -> str:
    if attempt <= 1:
        return ""
    return (
        "\n\nRETRY: Your earlier response failed strict JSON validation. "
        "Return precisely four JSON fields (title, description, highlights, "
        "search_terms), with no extra keys. Title 40-150 characters; "
        "description 70-110 natural English words based only on visible design; "
        "3-5 nonempty highlights; <=10 unique short search terms with <=180 "
        "combined characters. No unverified facts, brand claims, URLs or "
        "US manufacturing/shipping claims. Output a single JSON object."
    )


def attach_ytrends_evidence(draft: dict, evidence: dict) -> dict:
    """Preserve source/provenance for UI, never mark TikTok search verified."""
    status = evidence.get("status")
    if status not in ("connected", "no_data", "unavailable", "disabled"):
        evidence = empty_evidence("")
        status = "unavailable"
    warnings = [warn for warn in (draft.get("warnings") or [])
                if isinstance(warn, str) and not warn.startswith("ytrends_")]
    if status == "connected":
        warnings.append("ytrends_etsy_snapshot_not_tiktok_search")
        if (evidence.get("median_data_age_days") is not None
                and evidence["median_data_age_days"] > 30):
            warnings.append("ytrends_etsy_snapshot_data_older_than_30_days")
    elif status == "no_data":
        warnings.append("ytrends_no_indexed_etsy_results")
    elif status == "unavailable":
        warnings.append("ytrends_mcp_unavailable")
    else:
        warnings.append("ytrends_mcp_disabled")
    return {
        **draft, "ytrends_status": status,
        "ytrends_evidence": evidence,
        # The actual generated terms are still from the photo and prompt.
        "keyword_source": "visual_suggestions_with_etsy_context" if status == "connected"
                          else "unverified_visual_suggestions",
        "warnings": warnings,
    }


def queue_on_pick(db: Session, tenant: str, listing: EtsySpyListing,
                  picked_at: datetime, *, automatic: bool = False) -> ProcessingJobModel | None:
    """One immutable, idempotent job per user pick and source photo revision."""
    if not is_enabled() or (automatic and not auto_on_pick_enabled()):
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


def retry_failed_draft(db: Session, tenant: str, listing: EtsySpyListing,
                       picked_at: datetime) -> ProcessingJobModel | None:
    """Explicit retry of a terminal draft. Never revive a cancelled generation.

    Keep a short failure history in the payload; reset attempt_count only once
    the old job is terminal and the concurrency reservation is released.
    """
    if not is_enabled():
        return None
    job = queue_on_pick(db, tenant, listing, picked_at)
    if job is None:
        return None
    locked = db.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.id == job.id,
        ProcessingJobModel.tenant_id == tenant,
        ProcessingJobModel.job_type == JOB_TYPE,
    ).with_for_update())
    if locked is None or locked.cancellation_requested or locked.concurrency_accounted:
        return job
    if locked.status != "failed":
        return job
    payload = dict(locked.payload_json or {})
    count = int(payload.get("manual_retry_count") or 0)
    if count >= 2 or isinstance(payload.get("result"), dict):
        return job
    errors = list(payload.get("prior_errors") or [])[-1:]
    errors.append({"code": locked.last_error_code,
                   "at": datetime.now(timezone.utc).isoformat()})
    locked.payload_json = {**payload, "manual_retry_count": count + 1,
                           "prior_errors": errors}
    locked.status = "pending"
    locked.attempt_count = 0
    locked.claimed_by = None
    locked.claimed_at = None
    locked.lease_expires_at = None
    locked.last_error_code = None
    locked.last_error_message = None
    locked.next_attempt_at = datetime.now(timezone.utc)
    return locked


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
    def __init__(self, *, fetch_image=download_image, research_ytrends=None):
        self.fetch_image = fetch_image
        self.research_ytrends = research_ytrends or self._research_ytrends

    @staticmethod
    async def _research_ytrends(title: str) -> dict:
        settings = get_settings()
        return await research_etsy_keyword(
            title, enabled=settings.ETSY_TIKTOK_YTRENDS_ENABLED,
            bearer_token=settings.ETSY_TIKTOK_YTRENDS_BEARER_TOKEN.get_secret_value(),
            timeout_seconds=settings.ETSY_TIKTOK_YTRENDS_TIMEOUT_SECONDS,
        )

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult | DeferredJobOutcome:
        try:
            return asyncio.run(self._run(context))
        except AiProviderError as exc:
            deferred = deferred_rrugc_ai_retry(
                exc, message="TikTok listing waiting for Gemini model quota.")
            if deferred is not None:
                return deferred
            return (JobHandlerResult.retryable if exc.retryable
                    else JobHandlerResult.non_retryable)(
                        "tiktok_listing_provider_unavailable",
                        "TikTok listing draft provider unavailable.")
        except GeneratedListingInvalid as exc:
            return JobHandlerResult.retryable(
                "tiktok_listing_invalid_output",
                f"AI draft rejected by structured QA ({exc.reason}); retry automatically.")
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
        slot = scheduled_rrugc_gemini_slot(job)
        if slot is None:
            return JobHandlerResult.non_retryable(
                "tiktok_listing_model_slot_missing", "Gemini reservation is missing.")
        provider = context.dependencies.ai_provider_registry.require("gemini")
        try:
            evidence = await self.research_ytrends(payload["source_title"])
        except Exception:
            # YTrends is optional external research; a failed request must not
            # turn a valid Gemini draft into a retry or expose remote errors.
            evidence = empty_evidence("")
        if context.is_cancelled:
            return JobHandlerResult.cancelled()
        response = await provider.analyze_single(AiMetadataAnalysisInput(
            tenant_id=job.tenant_id, asset_id=job.id,
            prompt=(PROMPT + _retry_instruction(job.attempt_count) +
                    prompt_context(evidence) +
                    "\n\nUntrusted competitor title: " + payload["source_title"][:700]),
            image_bytes=photo, image_mime_type=mime,
            metadata_profile="etsy_tiktok_listing_draft",
            metadata_profile_version=PROFILE_VERSION,
            json_schema=ListingCopySchema.model_json_schema(),
            analysis_id=job.id,
            preferred_model=slot.model,
            preferred_credential_provider=slot.credential_provider,
            is_cancelled=lambda: context.is_cancelled,
        ))
        try:
            validated = attach_ytrends_evidence(
                validate_copy(prepare_generated_copy(response.metadata)), evidence)
        except (ValueError, TypeError) as exc:
            raise GeneratedListingInvalid(_safe_validation_reason(exc)) from exc
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