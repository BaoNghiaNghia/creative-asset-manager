from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

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
from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel,
    RrugcCandidateModel,
)
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.realistic_review_ugc.schema import CandidateSubmission


PIN_HOSTS = ("pinterest.com",)
IMAGE_HOSTS = ("pinimg.com",)


class RrugcError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


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
    return value


def validate_image_url(value: str) -> str:
    if not _hostname_allowed(value, IMAGE_HOSTS):
        raise RrugcError("invalid_pinterest_image_url", "Pinterest image URL is not allowed.")
    return value


def token_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def source_key(pin_url: str, image_url: str) -> str:
    return hashlib.sha256((pin_url + "\n" + image_url).encode("utf-8")).hexdigest()


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
        raise RrugcError("unsupported_image_format", "Downloaded image format is unsupported.") from exc


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
    ) -> tuple[RrugcCampaignModel, str]:
        raw_token = secrets.token_urlsafe(32)
        row = RrugcCampaignModel(
            tenant_id=tenant_id,
            name=name.strip(),
            query=query.strip(),
            target_count=target_count,
            max_scroll_batches=max_scroll_batches,
            auto_import=auto_import,
            status="running",
            scout_token_hash=token_digest(raw_token),
            created_by_user_id=user_id,
        )
        self.repository.add_campaign(row)
        self.session.commit()
        self.session.refresh(row)
        return row, raw_token

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
            if row is not None:
                existing += 1
                rows.append(row)
                continue
            row = RrugcCandidateModel(
                tenant_id=campaign.tenant_id,
                campaign_id=campaign.id,
                source_key=key,
                pin_url=pin_url,
                image_url=image_url,
                alt_text=(item.alt_text or "").strip() or None,
                status="discovered",
            )
            try:
                with self.session.begin_nested():
                    self.session.add(row)
                    self.session.flush()
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

    async def import_candidate(
        self,
        *,
        candidate: RrugcCandidateModel,
        storage: AssetStorageProvider,
        downloader: SecureImageDownloader | None = None,
    ) -> RrugcCandidateModel:
        if candidate.status == "drive_ready":
            return candidate
        candidate.status = "importing"
        candidate.last_error_code = None
        self.session.commit()
        active_downloader = downloader or SecureImageDownloader(
            DownloaderConfig(
                hostname_allowlist=IMAGE_HOSTS,
                max_response_bytes=20 * 1024 * 1024,
                max_width=10000,
                max_height=10000,
                max_pixels=50_000_000,
            ),
            enabled=True,
        )
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
                self.session.commit()
                self.session.refresh(candidate)
                return candidate
        except (UnsafeUrlError, DownloadLimitError, InvalidImageError) as exc:
            candidate.status = "import_failed"
            candidate.last_error_code = exc.__class__.__name__
            self.session.commit()
            raise RrugcError("reference_import_rejected", str(exc), status_code=422) from exc
        except (SecureDownloadError, StorageProviderError) as exc:
            candidate.status = "import_failed"
            candidate.last_error_code = getattr(exc, "code", exc.__class__.__name__)
            self.session.commit()
            raise RrugcError(
                "reference_import_failed", "Reference import failed.", status_code=502
            ) from exc
