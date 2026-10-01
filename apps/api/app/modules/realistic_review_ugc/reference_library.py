from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.providers.contracts import (
    AssetStorageProvider,
    StorageProviderError,
    StoreAssetInput,
)
from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel,
    RrugcCandidateModel,
    RrugcReferenceAssetModel,
)
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.visual_search.preprocess import (
    VisualImagePreparationError,
    VisualPreprocessLimits,
    decode_visual_image,
)


REFERENCE_PROFILE_REALISTIC_PERSON_UGC = "realistic-person-ugc"
REFERENCE_ASSET_MAX_BYTES = 20_000_000
REFERENCE_TYPES = frozenset({"person", "product", "scene", "detail", "artwork", "other"})


class ReferenceLibraryError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable


@dataclass(frozen=True)
class ReferenceAssetPromotion:
    asset: RrugcReferenceAssetModel
    created: bool


def _reference_type(candidate: RrugcCandidateModel) -> str:
    if int(candidate.people_count or 0) > 0:
        return "person"
    return "other"


def _context_score(candidate: RrugcCandidateModel) -> float | None:
    signal = candidate.ai_signal_json if isinstance(candidate.ai_signal_json, dict) else {}
    match = signal.get("context_match")
    if not isinstance(match, dict):
        return None
    value = match.get("score")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _campaign_themes(campaign: RrugcCampaignModel | None) -> list[str]:
    context_profile = (
        campaign.product_context_json
        if campaign is not None and isinstance(campaign.product_context_json, dict)
        else {}
    )
    return [
        str(value).strip()
        for value in context_profile.get("themes") or []
        if str(value or "").strip()
    ][:16]


def reference_image_content_type(image_format: str | None) -> tuple[str, str]:
    mapping = {
        "JPEG": ("image/jpeg", ".jpg"),
        "MPO": ("image/jpeg", ".jpg"),
        "PNG": ("image/png", ".png"),
        "WEBP": ("image/webp", ".webp"),
        "BMP": ("image/bmp", ".bmp"),
        "GIF": ("image/gif", ".gif"),
        "TIFF": ("image/tiff", ".tif"),
        "AVIF": ("image/avif", ".avif"),
        "HEIF": ("image/heif", ".heif"),
        "HEIC": ("image/heic", ".heic"),
    }
    result = mapping.get(str(image_format or "").upper())
    if result is None:
        raise ReferenceLibraryError(
            "reference_asset_format_unsupported",
            "Reference image format is unsupported.",
            status_code=422,
        )
    return result


async def _bytes_body(
    content: bytes,
    chunk_size: int = 64 * 1024,
) -> AsyncIterator[bytes]:
    for offset in range(0, len(content), chunk_size):
        yield content[offset : offset + chunk_size]


class RrugcReferenceLibrary:
    """Generic durable reference layer shared by Pinterest and user uploads."""

    def __init__(self, session: Session):
        self.session = session
        self.repository = RrugcRepository(session)

    def promote_pinterest_candidate(
        self,
        *,
        tenant_id: str,
        user_id: str,
        campaign: RrugcCampaignModel,
        candidate: RrugcCandidateModel,
    ) -> ReferenceAssetPromotion:
        if campaign.tenant_id != tenant_id or candidate.tenant_id != tenant_id:
            raise ReferenceLibraryError(
                "reference_asset_scope_mismatch",
                "Reference source does not belong to the active tenant.",
                status_code=404,
            )
        if candidate.campaign_id != campaign.id:
            raise ReferenceLibraryError(
                "reference_asset_source_mismatch",
                "Reference candidate does not belong to this campaign.",
                status_code=404,
            )
        if candidate.status != "drive_ready":
            raise ReferenceLibraryError(
                "reference_asset_not_ready",
                "Only a durable ready candidate can enter the Reference Library.",
                status_code=409,
            )
        if not candidate.content_hash or not candidate.remote_file_id:
            raise ReferenceLibraryError(
                "reference_asset_storage_incomplete",
                "Ready candidate is missing durable storage identity.",
                status_code=409,
            )

        existing = self.repository.reference_asset_by_source(
            tenant_id,
            "pinterest",
            candidate.source_key,
        )
        if existing is None:
            existing = self.repository.reference_asset_by_content_hash(
                tenant_id,
                candidate.content_hash,
            )
        if existing is not None:
            return ReferenceAssetPromotion(asset=existing, created=False)

        row = RrugcReferenceAssetModel(
            tenant_id=tenant_id,
            source_type="pinterest",
            source_key=candidate.source_key,
            source_url=candidate.pin_url,
            source_campaign_id=campaign.id,
            source_candidate_id=candidate.id,
            profile_key=REFERENCE_PROFILE_REALISTIC_PERSON_UGC,
            reference_type=_reference_type(candidate),
            status="ready",
            content_hash=candidate.content_hash,
            width=candidate.width,
            height=candidate.height,
            size_bytes=candidate.size_bytes,
            image_format=candidate.image_format,
            tags_json=[],
            themes_json=_campaign_themes(campaign),
            quality_score=candidate.quality_score,
            visual_score=None,
            context_score=_context_score(candidate),
            usage_count=0,
            remote_file_id=candidate.remote_file_id,
            remote_folder_id=candidate.remote_folder_id,
            web_url=candidate.web_url,
            created_by_user_id=user_id,
        )
        try:
            self.repository.add_reference_asset(row)
            self.session.commit()
            self.session.refresh(row)
            return ReferenceAssetPromotion(asset=row, created=True)
        except IntegrityError:
            self.session.rollback()
            existing = self.repository.reference_asset_by_source(
                tenant_id,
                "pinterest",
                candidate.source_key,
            )
            if existing is None:
                existing = self.repository.reference_asset_by_content_hash(
                    tenant_id,
                    candidate.content_hash,
                )
            if existing is None:
                raise
            return ReferenceAssetPromotion(asset=existing, created=False)

    async def upload_reference(
        self,
        *,
        tenant_id: str,
        user_id: str,
        original_filename: str | None,
        content: bytes,
        storage: AssetStorageProvider,
        reference_type: str = "other",
        campaign: RrugcCampaignModel | None = None,
        profile_key: str = REFERENCE_PROFILE_REALISTIC_PERSON_UGC,
        tags: list[str] | None = None,
        themes: list[str] | None = None,
    ) -> ReferenceAssetPromotion:
        normalized_type = str(reference_type or "other").strip().lower()
        if normalized_type not in REFERENCE_TYPES:
            raise ReferenceLibraryError(
                "reference_asset_type_invalid",
                "Reference asset type is invalid.",
                status_code=422,
            )
        if campaign is not None and campaign.tenant_id != tenant_id:
            raise ReferenceLibraryError(
                "reference_asset_scope_mismatch",
                "Reference campaign does not belong to the active tenant.",
                status_code=404,
            )
        if not content:
            raise ReferenceLibraryError(
                "reference_asset_empty",
                "Reference image is empty.",
                status_code=422,
            )
        if len(content) > REFERENCE_ASSET_MAX_BYTES:
            raise ReferenceLibraryError(
                "reference_asset_too_large",
                "Reference image exceeds the 20 MB limit.",
                status_code=413,
            )

        try:
            prepared = decode_visual_image(
                content,
                limits=VisualPreprocessLimits(
                    max_source_bytes=REFERENCE_ASSET_MAX_BYTES,
                    max_source_width=20_000,
                    max_source_height=20_000,
                    max_decode_pixels=100_000_000,
                ),
            )
        except VisualImagePreparationError as exc:
            raise ReferenceLibraryError(
                "reference_asset_invalid_image",
                str(exc),
                status_code=422,
            ) from exc
        try:
            image_format = prepared.source_format
            width = prepared.width
            height = prepared.height
        finally:
            prepared.image.close()

        content_hash = hashlib.sha256(content).hexdigest()
        existing = self.repository.reference_asset_by_content_hash(
            tenant_id,
            content_hash,
        )
        if existing is not None:
            return ReferenceAssetPromotion(asset=existing, created=False)

        content_type, suffix = reference_image_content_type(image_format)
        reference_id = str(uuid4())
        reusable = self.repository.reusable_product_reference_by_hash(
            tenant_id,
            content_hash,
        )
        if reusable is not None:
            remote_file_id = reusable.remote_file_id
            remote_folder_id = reusable.remote_folder_id
            web_url = reusable.web_url
        else:
            try:
                stored = await storage.store_asset(
                    StoreAssetInput(
                        tenant_id=tenant_id,
                        content_hash=content_hash,
                        body=_bytes_body(content),
                        asset_id=reference_id,
                        content_type=content_type,
                        size_bytes=len(content),
                        filename=(
                            f"REFERENCE_{normalized_type}_{reference_id}{suffix}"
                        ),
                    )
                )
            except StorageProviderError as exc:
                raise ReferenceLibraryError(
                    "reference_asset_storage_failed",
                    "Reference image could not be saved to Managed Storage.",
                    status_code=503,
                    retryable=bool(exc.retryable),
                ) from exc
            remote_file_id = stored.remote_file_id
            remote_folder_id = stored.remote_folder_id
            web_url = stored.web_url

        normalized_tags = [
            str(value).strip()
            for value in tags or []
            if str(value or "").strip()
        ][:32]
        normalized_themes = [
            str(value).strip()
            for value in (
                themes
                if themes is not None
                else _campaign_themes(campaign)
            )
            if str(value or "").strip()
        ][:16]
        row = RrugcReferenceAssetModel(
            id=reference_id,
            tenant_id=tenant_id,
            source_type="upload",
            source_key=content_hash,
            source_url=None,
            original_filename=(original_filename or "").strip()[:500] or None,
            source_campaign_id=campaign.id if campaign is not None else None,
            source_candidate_id=None,
            profile_key=(profile_key or REFERENCE_PROFILE_REALISTIC_PERSON_UGC)[:100],
            reference_type=normalized_type,
            status="ready",
            content_hash=content_hash,
            width=width,
            height=height,
            size_bytes=len(content),
            image_format=image_format,
            tags_json=normalized_tags,
            themes_json=normalized_themes,
            quality_score=None,
            visual_score=None,
            context_score=None,
            usage_count=0,
            remote_file_id=remote_file_id,
            remote_folder_id=remote_folder_id,
            web_url=web_url,
            created_by_user_id=user_id,
        )
        try:
            self.repository.add_reference_asset(row)
            self.session.commit()
            self.session.refresh(row)
            return ReferenceAssetPromotion(asset=row, created=True)
        except IntegrityError:
            self.session.rollback()
            existing = self.repository.reference_asset_by_content_hash(
                tenant_id,
                content_hash,
            )
            if existing is None:
                raise
            return ReferenceAssetPromotion(asset=existing, created=False)
