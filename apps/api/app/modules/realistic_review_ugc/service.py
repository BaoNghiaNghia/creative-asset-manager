from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.providers.contracts import (
    AssetStorageProvider,
    StorageProviderError,
    StoreAssetInput,
)
from app.infrastructure.downloader.secure_image import (
    DownloadLimitError,
    DownloaderConfig,
    InvalidImageError,
    SecureDownloadError,
    SecureImageDownloader,
    UnsafeUrlError,
)
from app.modules.processing.repository import ProcessingRepository
from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel,
    RrugcCandidateModel,
)
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.realistic_review_ugc.schema import CandidateSubmission


PIN_HOSTS = ("pinterest.com",)
IMAGE_HOSTS = ("pinimg.com",)
ANALYZE_JOB_TYPE = "rrugc_candidate_analyze"
IMPORT_JOB_TYPE = "rrugc_candidate_import"


class RrugcError(RuntimeError):
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


def _hostname_allowed(value: str, roots: tuple[str, ...]) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme.lower() != "https" or parsed.username or parsed.password:
        return False
    hostname = (parsed.hostname or "").rstrip(".").lower()
    return bool(hostname) and any(
        hostname == root or hostname.endswith("." + root) for root in roots
    )


def validate_pin_url(value: str) -> str:
    if not _hostname_allowed(value, PIN_HOSTS):
        raise RrugcError("invalid_pinterest_pin_url", "Pinterest pin URL is not allowed.")
    parsed = urlsplit(value)
    if not parsed.path.startswith("/pin/"):
        raise RrugcError("invalid_pinterest_pin_url", "Pinterest pin URL is not allowed.")
    path = parsed.path.rstrip("/") + "/"
    return urlunsplit(("https", "www.pinterest.com", path, "", ""))


def validate_image_url(value: str) -> str:
    if not _hostname_allowed(value, IMAGE_HOSTS):
        raise RrugcError("invalid_pinterest_image_url", "Pinterest image URL is not allowed.")
    return value


def token_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def source_key(pin_url: str, _image_url: str = "") -> str:
    # Pinterest may expose the same Pin through several CDN rendition URLs.
    # Pin identity is the stable source identity; content hash still protects
    # cross-Pin byte duplication during import.
    return hashlib.sha256(pin_url.encode("utf-8")).hexdigest()


def campaign_token_matches(row: RrugcCampaignModel, raw_token: str) -> bool:
    if not raw_token:
        return False
    return hmac.compare_digest(row.scout_token_hash, token_digest(raw_token))


def _content_type(image_format: str) -> tuple[str, str]:
    mapping = {
        "JPEG": ("image/jpeg", ".jpg"),
        "PNG": ("image/png", ".png"),
        "WEBP": ("image/webp", ".webp"),
        "GIF": ("image/gif", ".gif"),
        "TIFF": ("image/tiff", ".tif"),
        "BMP": ("image/bmp", ".bmp"),
    }
    try:
        return mapping[image_format.upper()]
    except KeyError as exc:
        raise RrugcError(
            "unsupported_image_format", "Downloaded image format is unsupported."
        ) from exc


def build_reference_downloader() -> SecureImageDownloader:
    return SecureImageDownloader(
        DownloaderConfig(
            hostname_allowlist=IMAGE_HOSTS,
            max_response_bytes=20 * 1024 * 1024,
            max_width=10000,
            max_height=10000,
            max_pixels=50_000_000,
        ),
        enabled=True,
    )


async def _file_body(path: Path, chunk_size: int = 64 * 1024) -> AsyncIterator[bytes]:
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                return
            yield chunk


class RrugcService:
    def __init__(self, session: Session):
        self.session = session
        self.repository = RrugcRepository(session)
        self.processing = ProcessingRepository(session)

    def create_campaign(
        self,
        *,
        tenant_id: str,
        user_id: str,
        name: str,
        query: str,
        target_count: int,
        max_scroll_batches: int,
        auto_import: bool,
        auto_scout: bool = True,
        scan_interval_seconds: int = 300,
        min_head_ratio: float = 0.20,
        max_head_ratio: float = 0.45,
        min_smile_score: float = 0.65,
        max_head_occlusion: float = 0.25,
        max_ai_risk_score: float = 0.20,
        min_quality_score: float = 0.55,
        min_ugc_score: float = 0.55,
        min_product_fit_score: float = 0.55,
        require_head_visible: bool = True,
        reject_headwear: bool = True,
    ) -> tuple[RrugcCampaignModel, str]:
        raw_token = secrets.token_urlsafe(32)
        row = RrugcCampaignModel(
            tenant_id=tenant_id,
            name=name.strip(),
            query=query.strip(),
            target_count=target_count,
            max_scroll_batches=max_scroll_batches,
            auto_import=auto_import,
            auto_scout=auto_scout,
            scan_interval_seconds=scan_interval_seconds,
            scan_next_at=datetime.now(timezone.utc) if auto_scout else None,
            min_head_ratio=min_head_ratio,
            max_head_ratio=max_head_ratio,
            min_smile_score=min_smile_score,
            max_head_occlusion=max_head_occlusion,
            max_ai_risk_score=max_ai_risk_score,
            min_quality_score=min_quality_score,
            min_ugc_score=min_ugc_score,
            min_product_fit_score=min_product_fit_score,
            require_head_visible=require_head_visible,
            reject_headwear=reject_headwear,
            status="running",
            scout_token_hash=token_digest(raw_token),
            created_by_user_id=user_id,
        )
        self.repository.add_campaign(row)
        self.session.commit()
        self.session.refresh(row)
        return row, raw_token

    def _analysis_job_key(self, candidate: RrugcCandidateModel) -> str:
        return f"rrugc-analyze:{candidate.id}:{candidate.analysis_revision}"

    def _import_job_key(self, candidate: RrugcCandidateModel) -> str:
        return f"rrugc-import:{candidate.id}:{candidate.import_revision}"

    def enqueue_analysis(
        self,
        candidate: RrugcCandidateModel,
        *,
        increment_revision: bool = False,
    ) -> None:
        if increment_revision:
            candidate.analysis_revision = max(1, int(candidate.analysis_revision or 0) + 1)
        elif not candidate.analysis_revision:
            candidate.analysis_revision = 1
        candidate.status = "analysis_queued"
        candidate.last_error_code = None
        candidate.reject_reason = None
        self.processing.create_job_once(
            tenant_id=candidate.tenant_id,
            job_type=ANALYZE_JOB_TYPE,
            entity_type="rrugc_candidate",
            entity_id=candidate.id,
            idempotency_key=self._analysis_job_key(candidate),
            payload={
                "candidate_id": candidate.id,
                "campaign_id": candidate.campaign_id,
                "analysis_revision": candidate.analysis_revision,
            },
            provider_key="gemini",
            provider_scope="ai",
            max_attempts=3,
            priority=55,
        )

    def enqueue_import(
        self,
        candidate: RrugcCandidateModel,
        *,
        retry: bool = False,
    ) -> None:
        if candidate.status == "drive_ready":
            return
        if candidate.status not in {"approved", "import_failed", "import_queued"}:
            raise RrugcError(
                "reference_not_approved",
                "Only an approved reference can be saved to Drive.",
                status_code=409,
            )
        if retry or not candidate.import_revision:
            candidate.import_revision = max(1, int(candidate.import_revision or 0) + 1)
        candidate.status = "import_queued"
        candidate.last_error_code = None
        self.processing.create_job_once(
            tenant_id=candidate.tenant_id,
            job_type=IMPORT_JOB_TYPE,
            entity_type="rrugc_candidate",
            entity_id=candidate.id,
            idempotency_key=self._import_job_key(candidate),
            payload={
                "candidate_id": candidate.id,
                "campaign_id": candidate.campaign_id,
                "import_revision": candidate.import_revision,
            },
            provider_key="google_drive",
            provider_scope="storage",
            max_attempts=5,
            priority=50,
        )

    def ingest_candidates(
        self,
        *,
        campaign: RrugcCampaignModel,
        submissions: list[CandidateSubmission],
    ) -> tuple[list[RrugcCandidateModel], int, int]:
        rows: list[RrugcCandidateModel] = []
        created = 0
        existing = 0
        for item in submissions:
            pin_url = validate_pin_url(item.pin_url)
            image_url = validate_image_url(item.image_url)
            key = source_key(pin_url, image_url)
            row = self.repository.candidate_by_source_key(
                campaign.tenant_id, campaign.id, key
            )
            if row is None:
                row = self.repository.candidate_by_pin_url(
                    campaign.tenant_id,
                    campaign.id,
                    pin_url,
                )
            if row is not None:
                existing += 1
                if row.status == "discovered":
                    self.enqueue_analysis(row)
                rows.append(row)
                continue
            row = RrugcCandidateModel(
                tenant_id=campaign.tenant_id,
                campaign_id=campaign.id,
                source_key=key,
                pin_url=pin_url,
                image_url=image_url,
                alt_text=(item.alt_text or "").strip() or None,
                status="analysis_queued",
                analysis_revision=1,
            )
            try:
                with self.session.begin_nested():
                    self.session.add(row)
                    self.session.flush()
                self.enqueue_analysis(row)
                created += 1
            except IntegrityError:
                row = self.repository.candidate_by_source_key(
                    campaign.tenant_id, campaign.id, key
                )
                if row is None:
                    raise
                existing += 1
            rows.append(row)
        campaign.scout_last_seen_at = datetime.now(timezone.utc)
        self.session.commit()
        for row in rows:
            self.session.refresh(row)
        return rows, created, existing

    def reanalyze_candidate(self, candidate: RrugcCandidateModel) -> RrugcCandidateModel:
        if candidate.status in {"drive_ready", "importing", "import_queued"}:
            raise RrugcError(
                "reference_analysis_locked",
                "A stored or importing reference cannot be re-analyzed.",
                status_code=409,
            )
        self.enqueue_analysis(candidate, increment_revision=True)
        self.session.commit()
        self.session.refresh(candidate)
        return candidate

    def queue_candidate_import(self, candidate: RrugcCandidateModel) -> RrugcCandidateModel:
        self.enqueue_import(candidate, retry=candidate.status == "import_failed")
        self.session.commit()
        self.session.refresh(candidate)
        return candidate

    def refresh_campaign_completion(self, campaign: RrugcCampaignModel) -> None:
        counts = self.repository.campaign_counts(campaign.tenant_id, campaign.id)
        progress = (
            counts.get("drive_ready", 0)
            if campaign.auto_import
            else counts.get("approved", 0)
        )
        if progress >= campaign.target_count:
            campaign.status = "completed"
            campaign.scout_status = "ready"

    async def import_candidate(
        self,
        *,
        candidate: RrugcCandidateModel,
        storage: AssetStorageProvider,
        downloader: SecureImageDownloader | None = None,
    ) -> RrugcCandidateModel:
        if candidate.status == "drive_ready":
            return candidate
        if candidate.status not in {"approved", "import_queued", "importing", "import_failed"}:
            raise RrugcError(
                "reference_not_approved",
                "Only an approved reference can be saved to Drive.",
                status_code=409,
            )
        candidate.status = "importing"
        candidate.last_error_code = None
        self.session.commit()
        active_downloader = downloader or build_reference_downloader()
        try:
            async with active_downloader.download(candidate.image_url) as image:
                duplicate = self.repository.drive_candidate_by_hash(
                    candidate.tenant_id, image.content_hash, candidate.id
                )
                if duplicate is not None:
                    candidate.status = "rejected_duplicate"
                    candidate.content_hash = image.content_hash
                    candidate.width = image.width
                    candidate.height = image.height
                    candidate.size_bytes = image.size_bytes
                    candidate.image_format = image.image_format
                    candidate.remote_file_id = duplicate.remote_file_id
                    candidate.remote_folder_id = duplicate.remote_folder_id
                    candidate.web_url = duplicate.web_url
                    candidate.imported_at = datetime.now(timezone.utc)
                    self.session.commit()
                    self.session.refresh(candidate)
                    return candidate

                content_type, suffix = _content_type(image.image_format)
                stored = await storage.store_asset(
                    StoreAssetInput(
                        tenant_id=candidate.tenant_id,
                        content_hash=image.content_hash,
                        body=_file_body(image.path),
                        asset_id=candidate.id,
                        content_type=content_type,
                        size_bytes=image.size_bytes,
                        filename=f"REF_{candidate.id}{suffix}",
                    )
                )
                candidate.status = "drive_ready"
                candidate.content_hash = image.content_hash
                candidate.width = image.width
                candidate.height = image.height
                candidate.size_bytes = image.size_bytes
                candidate.image_format = image.image_format
                candidate.remote_file_id = stored.remote_file_id
                candidate.remote_folder_id = stored.remote_folder_id
                candidate.web_url = stored.web_url
                candidate.imported_at = datetime.now(timezone.utc)
                campaign = self.repository.get_campaign(
                    candidate.tenant_id, candidate.campaign_id
                )
                if campaign is not None:
                    self.refresh_campaign_completion(campaign)
                self.session.commit()
                self.session.refresh(candidate)
                return candidate
        except (UnsafeUrlError, DownloadLimitError, InvalidImageError) as exc:
            candidate.status = "import_failed"
            candidate.last_error_code = exc.__class__.__name__
            self.session.commit()
            raise RrugcError(
                "reference_import_rejected",
                str(exc),
                status_code=422,
                retryable=False,
            ) from exc
        except (SecureDownloadError, httpx.HTTPError, StorageProviderError) as exc:
            candidate.status = "import_failed"
            candidate.last_error_code = getattr(exc, "code", exc.__class__.__name__)
            self.session.commit()
            retryable = bool(getattr(exc, "retryable", True))
            raise RrugcError(
                "reference_import_failed",
                "Reference import failed.",
                status_code=502,
                retryable=retryable,
            ) from exc
