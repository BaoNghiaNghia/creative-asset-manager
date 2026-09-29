from __future__ import annotations

import hashlib
import hmac
import re
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
from app.modules.realistic_review_ugc.analysis import reference_preference_features
from app.modules.realistic_review_ugc.keyword_strategy import (
    build_campaign_search_queries,
    campaign_learning_intent,
)
from app.modules.realistic_review_ugc.model import (
    RrugcAiFeedbackModel,
    RrugcCampaignModel,
    RrugcCandidateModel,
)
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.realistic_review_ugc.schema import CandidateSubmission


PIN_HOSTS = ("pinterest.com",)
IMAGE_HOSTS = ("pinimg.com",)
ANALYZE_JOB_TYPE = "rrugc_candidate_analyze"
IMPORT_JOB_TYPE = "rrugc_candidate_import"
SYNTHETIC_SOURCE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bai[- ]generated\b",
        r"\bartificial intelligence\b",
        r"\bmidjourney\b",
        r"\bstable diffusion\b",
        r"\bdall[- ]?e\b",
        r"\bflux ai\b",
        r"\bleonardo ai\b",
        r"\bideogram\b",
        r"\bdigital (?:art|illustration)\b",
        r"\b3d (?:render|rendering|art)\b",
        r"\bcgi\b",
        r"\bconcept art\b",
        r"\bvector (?:art|illustration)\b",
        r"\banime\b",
        r"\bcartoon\b",
        r"\bgenerative art\b",
        r"\bai art\b",
        r"\bprompt\b",
    )
)


def source_metadata_looks_synthetic(value: str | None) -> bool:
    text = str(value or "").strip()
    return bool(text and any(pattern.search(text) for pattern in SYNTHETIC_SOURCE_PATTERNS))


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
        search_queries: list[str] | None = None,
        auto_scout: bool = True,
        scan_interval_seconds: int = 300,
        min_head_ratio: float = 0.18,
        max_head_ratio: float = 0.70,
        min_smile_score: float = 0.00,
        max_head_occlusion: float = 0.65,
        max_ai_risk_score: float = 0.15,
        min_quality_score: float = 0.60,
        min_ugc_score: float = 0.55,
        min_product_fit_score: float = 0.55,
        require_head_visible: bool = True,
        reject_headwear: bool = False,
    ) -> tuple[RrugcCampaignModel, str]:
        raw_token = secrets.token_urlsafe(32)
        queries: list[str] = []
        seen: set[str] = set()
        for raw in [query, *(search_queries or [])]:
            value = str(raw or "").strip()
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                queries.append(value)
        if not queries:
            raise RrugcError(
                "rrugc_search_query_required",
                "At least one Pinterest search query is required.",
                status_code=400,
            )
        anchors = list(queries)
        queries = build_campaign_search_queries(
            name=name,
            queries=anchors,
            protected_queries=anchors,
            reject_headwear=reject_headwear,
        )
        row = RrugcCampaignModel(
            tenant_id=tenant_id,
            name=name.strip(),
            query=queries[0],
            search_queries_json=queries,
            search_query_anchors_json=anchors,
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

    def update_campaign(
        self,
        campaign: RrugcCampaignModel,
        *,
        name: str | None = None,
        search_queries: list[str] | None = None,
        target_count: int | None = None,
        max_scroll_batches: int | None = None,
        auto_import: bool | None = None,
        auto_scout: bool | None = None,
        scan_interval_seconds: int | None = None,
        min_head_ratio: float | None = None,
        max_head_ratio: float | None = None,
        min_smile_score: float | None = None,
        max_head_occlusion: float | None = None,
        max_ai_risk_score: float | None = None,
        min_quality_score: float | None = None,
        min_ugc_score: float | None = None,
        min_product_fit_score: float | None = None,
        require_head_visible: bool | None = None,
        reject_headwear: bool | None = None,
    ) -> RrugcCampaignModel:
        if name is not None:
            campaign.name = name.strip()

        if search_queries is not None:
            queries: list[str] = []
            seen: set[str] = set()
            for raw in search_queries:
                value = str(raw or "").strip()
                key = value.casefold()
                if value and key not in seen:
                    seen.add(key)
                    queries.append(value)
            if not queries:
                raise RrugcError(
                    "rrugc_search_query_required",
                    "At least one Pinterest search query is required.",
                    status_code=400,
                )
            campaign.query = queries[0]
            campaign.search_queries_json = queries
            campaign.search_query_anchors_json = list(queries)

        for field, value in {
            "target_count": target_count,
            "max_scroll_batches": max_scroll_batches,
            "scan_interval_seconds": scan_interval_seconds,
            "min_head_ratio": min_head_ratio,
            "max_head_ratio": max_head_ratio,
            "min_smile_score": min_smile_score,
            "max_head_occlusion": max_head_occlusion,
            "max_ai_risk_score": max_ai_risk_score,
            "min_quality_score": min_quality_score,
            "min_ugc_score": min_ugc_score,
            "min_product_fit_score": min_product_fit_score,
            "require_head_visible": require_head_visible,
            "reject_headwear": reject_headwear,
        }.items():
            if value is not None:
                setattr(campaign, field, value)

        if campaign.min_head_ratio >= campaign.max_head_ratio:
            raise RrugcError(
                "rrugc_head_ratio_invalid",
                "Minimum head ratio must be lower than maximum head ratio.",
                status_code=400,
            )

        if auto_import is not None:
            campaign.auto_import = auto_import
        if auto_scout is not None:
            campaign.auto_scout = auto_scout
            if not auto_scout:
                campaign.scan_next_at = None

        anchor_queries = list(
            campaign.search_query_anchors_json
            or campaign.search_queries_json
            or [campaign.query]
        )
        refreshed_queries = build_campaign_search_queries(
            name=campaign.name,
            queries=anchor_queries,
            protected_queries=anchor_queries,
            product_snapshot=campaign.product_snapshot_json,
            reject_headwear=campaign.reject_headwear,
        )
        if refreshed_queries:
            campaign.query = refreshed_queries[0]
            campaign.search_queries_json = refreshed_queries

        if campaign.auto_scout and campaign.status == "running":
            campaign.scan_next_at = datetime.now(timezone.utc)
            campaign.scan_empty_streak = 0
            campaign.scan_last_error_code = None

        self.session.commit()
        self.session.refresh(campaign)
        return campaign

    def archive_campaign(self, campaign: RrugcCampaignModel) -> RrugcCampaignModel:
        campaign.status = "archived"
        campaign.auto_scout = False
        campaign.scan_next_at = None
        campaign.scan_lease_agent_id = None
        campaign.scan_lease_run_id = None
        campaign.scan_lease_expires_at = None
        self.session.commit()
        self.session.refresh(campaign)
        return campaign

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
        source_query: str | None = None,
    ) -> tuple[list[RrugcCandidateModel], int, int]:
        rows: list[RrugcCandidateModel] = []
        created = 0
        existing = 0
        for item in submissions:
            if source_metadata_looks_synthetic(item.alt_text):
                continue
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

            tenant_duplicate = self.repository.analyzed_candidate_by_source_key_tenant(
                campaign.tenant_id,
                key,
                exclude_campaign_id=campaign.id,
            )
            if tenant_duplicate is not None:
                duplicate_signal = {
                    "duplicate_exact_candidate_id": tenant_duplicate.id,
                    "duplicate_exact_campaign_id": tenant_duplicate.campaign_id,
                    "duplicate_exact_source_key": key,
                }
                if source_query:
                    duplicate_signal["scout_query"] = source_query
                row = RrugcCandidateModel(
                    tenant_id=campaign.tenant_id,
                    campaign_id=campaign.id,
                    source_key=key,
                    pin_url=pin_url,
                    image_url=image_url,
                    alt_text=(item.alt_text or "").strip() or None,
                    ai_signal_json=duplicate_signal,
                    status="rejected_duplicate",
                    analysis_revision=1,
                    reject_reason="EXACT_SOURCE_DUPLICATE",
                )
                try:
                    with self.session.begin_nested():
                        self.session.add(row)
                        self.session.flush()
                except IntegrityError:
                    row = self.repository.candidate_by_source_key(
                        campaign.tenant_id,
                        campaign.id,
                        key,
                    )
                    if row is None:
                        raise
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
                ai_signal_json={"scout_query": source_query} if source_query else None,
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

    def mark_candidate_ai_label(
        self,
        candidate: RrugcCandidateModel,
        *,
        label: str,
        note: str | None,
        user_id: str,
    ) -> RrugcCandidateModel:
        if label not in {"real", "ai", "unsure"}:
            raise RrugcError(
                "invalid_ai_feedback_label",
                "AI feedback label must be real, ai, or unsure.",
                status_code=422,
            )
        now = datetime.now(timezone.utc)
        clean_note = (note or "").strip() or None
        if clean_note is not None:
            clean_note = clean_note[:1000]

        self.repository.add_ai_feedback(
            RrugcAiFeedbackModel(
                tenant_id=candidate.tenant_id,
                campaign_id=candidate.campaign_id,
                candidate_id=candidate.id,
                label=label,
                note=clean_note,
                ai_risk_raw_score=candidate.ai_risk_raw_score,
                ai_risk_score=candidate.ai_risk_score,
                detector_confidence=candidate.ai_detector_confidence,
                analyzer_version=candidate.analyzer_version,
                signal_json=candidate.ai_signal_json,
                created_by_user_id=user_id,
                created_at=now,
            )
        )
        candidate.ai_manual_label = label
        candidate.ai_manual_note = clean_note
        candidate.ai_manual_reviewed_by_user_id = user_id
        candidate.ai_manual_reviewed_at = now

        locked = candidate.status in {"drive_ready", "importing", "import_queued"}
        if not locked and label == "ai":
            candidate.status = "rejected_ai_risk"
            candidate.reject_reason = "MANUAL_AI_LABEL"
            candidate.last_error_code = None
        elif not locked and label == "real" and candidate.status == "rejected_ai_risk":
            # Re-run the complete qualification policy. The analyzer will keep
            # the human "real" label authoritative for authenticity while
            # still enforcing head/quality/UGC/product-fit requirements.
            self.enqueue_analysis(candidate, increment_revision=True)

        self.session.commit()
        self.session.refresh(candidate)
        return candidate

    def mark_candidate_reference_label(
        self,
        candidate: RrugcCandidateModel,
        *,
        label: str,
        note: str | None,
        user_id: str,
    ) -> RrugcCandidateModel:
        if label not in {"good", "bad", "clear"}:
            raise RrugcError(
                "invalid_reference_feedback_label",
                "Reference feedback label must be good, bad, or clear.",
                status_code=422,
            )
        now = datetime.now(timezone.utc)
        clean_note = (note or "").strip() or None
        if clean_note is not None:
            clean_note = clean_note[:1000]

        features = reference_preference_features(
            phone_authenticity_score=candidate.phone_authenticity_score,
            mobile_ugc_score=candidate.mobile_ugc_score,
            product_fit_score=candidate.product_fit_score,
            quality_score=candidate.quality_score,
            artistic_editorial_risk=candidate.artistic_editorial_risk,
            ai_risk_score=candidate.ai_risk_score,
        )
        signal = dict(candidate.ai_signal_json or {})
        campaign = self.repository.get_campaign(
            candidate.tenant_id,
            candidate.campaign_id,
        )
        learning_intent = campaign_learning_intent(
            campaign_id=candidate.campaign_id,
            name=campaign.name if campaign is not None else "",
            queries=(
                list(campaign.search_queries_json or [campaign.query])
                if campaign is not None
                else [signal.get("scout_query") or ""]
            ),
            product_snapshot=(
                campaign.product_snapshot_json
                if campaign is not None
                else None
            ),
        )
        ledger_label = {
            "good": "ref_good",
            "bad": "ref_bad",
            "clear": "ref_clear",
        }[label]
        self.repository.add_ai_feedback(
            RrugcAiFeedbackModel(
                tenant_id=candidate.tenant_id,
                campaign_id=candidate.campaign_id,
                candidate_id=candidate.id,
                label=ledger_label,
                note=clean_note,
                ai_risk_raw_score=candidate.ai_risk_raw_score,
                ai_risk_score=candidate.ai_risk_score,
                detector_confidence=candidate.ai_detector_confidence,
                analyzer_version=candidate.analyzer_version,
                signal_json={
                    "reference_preference_features": features,
                    "reference_preference_trainable": candidate.analyzed_at is not None,
                    "visual_fingerprints": signal.get("visual_fingerprints", []),
                    "diversity": signal.get("diversity"),
                    "scout_query": signal.get("scout_query"),
                    "learning_intent": learning_intent,
                },
                created_by_user_id=user_id,
                created_at=now,
            )
        )

        for key in (
            "reference_manual_label",
            "reference_manual_note",
            "reference_manual_reviewed_by_user_id",
            "reference_manual_reviewed_at",
        ):
            signal.pop(key, None)
        if label != "clear":
            signal.update({
                "reference_manual_label": label,
                "reference_manual_note": clean_note,
                "reference_manual_reviewed_by_user_id": user_id,
                "reference_manual_reviewed_at": now.isoformat(),
            })
        candidate.ai_signal_json = signal

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
