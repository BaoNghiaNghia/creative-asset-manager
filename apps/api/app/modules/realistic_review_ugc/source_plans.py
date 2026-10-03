from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Callable

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import (
    DeferredJobOutcome,
    JobHandlerContext,
    JobHandlerResult,
)
from app.domain.providers.contracts import (
    AiProviderError,
    OpenStoredAssetInput,
    StorageProviderError,
)
from app.domain.providers.registry import AiProviderUnavailableError
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.realistic_review_ugc.keyword_strategy import (
    build_campaign_search_queries,
)
from app.modules.realistic_review_ugc.gemini_safety import (
    scheduled_rrugc_gemini_slot,
)
from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel,
    RrugcSourcePlanModel,
)
from app.modules.realistic_review_ugc.product_context import (
    PRODUCT_VISUAL_CONTEXT_VERSION,
    analyze_product_visual_reference,
    derive_product_context_profile,
    merge_product_visual_context,
    product_context_search_queries,
    product_visual_binding_fingerprint,
)
from app.modules.realistic_review_ugc.service import RrugcService
from app.modules.storage.provider_factory import build_managed_storage_provider
from app.providers.ai.factory import build_ai_provider_registry
from app.providers.google.drive import GoogleDriveClient
from app.providers.google.storage import GoogleDriveAssetStorage

RRUGC_SOURCE_ROOT_FOLDER_ID = "1kNBQU4O-i6cbDBnRrhPGNENHvieWYPfX"
RRUGC_SOURCE_TARGET_COUNT = 50
RRUGC_SOURCE_PLAN_JOB_TYPE = "rrugc_source_plan_analyze"
RRUGC_SOURCE_MAX_FOLDERS = 5_000
RRUGC_SOURCE_MAX_IMAGES = 20_000
RRUGC_SOURCE_MAX_IMAGE_BYTES = 25_000_000


class RrugcSourcePlanError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class SourceImage:
    file_id: str
    parent_folder_id: str
    relative_path: str
    name: str
    mime_type: str
    size_bytes: int | None
    width: int | None
    height: int | None
    modified_at: datetime | None
    web_url: str | None
    revision: str


@dataclass(frozen=True, slots=True)
class SourcePlanSyncResult:
    root_folder_id: str
    folders_scanned: int
    images_found: int
    plans_created: int
    plans_updated: int
    plans_missing: int
    jobs_queued: int
    unchanged: int


def _source_revision(node: Any) -> str:
    modified = getattr(node, "modified_at", None)
    modified_value = (
        modified.astimezone(timezone.utc).isoformat()
        if isinstance(modified, datetime)
        else ""
    )
    payload = "|".join(
        (
            str(getattr(node, "id", "") or ""),
            modified_value,
            str(getattr(node, "size", "") or ""),
            str(getattr(node, "image_width", "") or ""),
            str(getattr(node, "image_height", "") or ""),
            str(getattr(node, "mime_type", "") or ""),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_source_image(node: Any) -> bool:
    return (
        str(getattr(node, "kind", "") or "") == "image"
        or str(getattr(node, "mime_type", "") or "").casefold().startswith("image/")
    )


async def discover_source_images(
    drive: GoogleDriveClient,
    *,
    root_folder_id: str = RRUGC_SOURCE_ROOT_FOLDER_ID,
) -> tuple[list[SourceImage], int]:
    """Recursively discover images below child folders of the configured root.

    Direct image children of the root are intentionally ignored. The source
    contract is root -> child folders -> descendant product/embroidery images.
    """
    root = await drive.get(root_folder_id)
    if root.kind != "folder":
        raise RrugcSourcePlanError(
            "rrugc_source_root_not_folder",
            "The configured Realistic Review UGC source is not a Google Drive folder.",
            status_code=409,
        )

    root_children = await drive.children(root_folder_id)
    queue: deque[tuple[str, str]] = deque()
    seen_folders: set[str] = {root_folder_id}
    for child in root_children:
        if child.kind == "folder" and child.id not in seen_folders:
            queue.append((child.id, child.name))

    rows: list[SourceImage] = []
    folders_scanned = 0
    while queue:
        folder_id, relative_folder = queue.popleft()
        if folder_id in seen_folders:
            continue
        seen_folders.add(folder_id)
        folders_scanned += 1
        if folders_scanned > RRUGC_SOURCE_MAX_FOLDERS:
            raise RrugcSourcePlanError(
                "rrugc_source_folder_limit",
                "The Realistic Review UGC source tree exceeds the safe folder scan limit.",
                status_code=409,
            )

        for child in await drive.children(folder_id):
            child_path = f"{relative_folder}/{child.name}"
            if child.kind == "folder":
                if child.id not in seen_folders:
                    queue.append((child.id, child_path))
                continue
            if not _is_source_image(child):
                continue
            rows.append(
                SourceImage(
                    file_id=child.id,
                    parent_folder_id=folder_id,
                    relative_path=child_path,
                    name=child.name,
                    mime_type=child.mime_type or "application/octet-stream",
                    size_bytes=child.size,
                    width=child.image_width,
                    height=child.image_height,
                    modified_at=child.modified_at,
                    web_url=child.web_url,
                    revision=_source_revision(child),
                )
            )
            if len(rows) > RRUGC_SOURCE_MAX_IMAGES:
                raise RrugcSourcePlanError(
                    "rrugc_source_image_limit",
                    "The Realistic Review UGC source tree exceeds the safe image scan limit.",
                    status_code=409,
                )

    rows.sort(key=lambda item: (item.relative_path.casefold(), item.file_id))
    return rows, folders_scanned


def _get_source_plan(
    session: Session,
    *,
    tenant_id: str,
    root_folder_id: str,
    source_file_id: str,
) -> RrugcSourcePlanModel | None:
    return session.scalar(
        select(RrugcSourcePlanModel).where(
            RrugcSourcePlanModel.tenant_id == tenant_id,
            RrugcSourcePlanModel.root_folder_id == root_folder_id,
            RrugcSourcePlanModel.source_file_id == source_file_id,
        )
    )


def list_source_plans(
    session: Session,
    *,
    tenant_id: str,
    limit: int = 2_000,
) -> list[RrugcSourcePlanModel]:
    return list(
        session.scalars(
            select(RrugcSourcePlanModel)
            .where(RrugcSourcePlanModel.tenant_id == tenant_id)
            .order_by(
                RrugcSourcePlanModel.source_relative_path.asc(),
                RrugcSourcePlanModel.id.asc(),
            )
            .limit(limit)
        )
    )

_EMBROIDERY_COLOR_WORDS = frozenset({
    "black", "white", "navy", "blue", "red", "green", "pink", "brown",
    "tan", "beige", "natural", "khaki", "gray", "grey", "charcoal",
    "orange", "yellow", "purple", "maroon", "cream", "teal",
})
_EMBROIDERY_GENERIC_WORDS = frozenset({
    "hat", "cap", "fabric", "cotton", "thread", "threads", "embroidery",
    "embroidered", "stitch", "stitched", "stitching", "design", "artwork",
    "front", "center", "centered", "visible", "product",
})
_EMBROIDERY_MOTIF_WORDS = frozenset({
    "dog", "dogs", "cat", "cats", "paw", "paws", "pet", "heart", "hearts",
    "bow", "bows", "flower", "flowers", "rose", "golf", "golfer", "club",
    "clubs", "ball", "bike", "bicycle", "motorcycle", "hotdog", "hotdogs",
    "sausage", "teacher", "apple", "grandpa", "grandma", "dad", "mom",
    "baby", "wedding", "bride", "groom", "mountain", "tree", "forest",
    "fish", "fishing", "beer", "coffee", "book", "books", "sun", "moon",
    "star", "stars", "skull", "cowboy", "horse", "truck", "tractor",
})


def _normalized_embroidery_tokens(value: Any, *, drop_generic: bool = False) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", str(value or "").casefold())
    if drop_generic:
        tokens = [
            token
            for token in tokens
            if token not in _EMBROIDERY_COLOR_WORDS
            and token not in _EMBROIDERY_GENERIC_WORDS
        ]
    return tokens


def embroidery_signature(visual_context: dict[str, Any] | None) -> str | None:
    """Return a color-invariant key for sharing Scout discovery across hat variants."""

    context = dict(visual_context or {})
    text_rows = [
        " ".join(_normalized_embroidery_tokens(value))
        for value in list(context.get("embroidery_text") or [])[:6]
    ]
    text_rows = sorted({value for value in text_rows if value})

    semantic_blob = " ".join(
        [
            str(context.get("embroidery_identity") or ""),
            str(context.get("summary") or ""),
            *[str(value) for value in list(context.get("product_cues") or [])[:10]],
        ]
    )
    semantic_tokens = _normalized_embroidery_tokens(
        semantic_blob,
        drop_generic=True,
    )
    motifs = sorted({
        token for token in semantic_tokens if token in _EMBROIDERY_MOTIF_WORDS
    })
    identity_value = str(context.get("embroidery_identity") or "").strip()
    identity_tokens = (
        sorted(set(_normalized_embroidery_tokens(identity_value, drop_generic=True)))[:24]
        if identity_value
        else []
    )

    # Readable embroidery text is the strongest stable identity signal.
    # Concrete motif tokens distinguish common same-wording designs without
    # making the key sensitive to hat color or free-form scene wording. For
    # text-free embroidery, the dedicated color-invariant identity is used;
    # older analyzed rows fall back to their color-stripped semantic cues.
    if text_rows:
        payload = {
            "text": text_rows,
            "motifs": motifs,
        }
    else:
        fallback_tokens = identity_tokens or sorted(set(semantic_tokens))[:24]
        if not motifs and not fallback_tokens:
            return None
        payload = {
            "text": [],
            "motifs": motifs,
            "identity": fallback_tokens,
        }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _lock_embroidery_group(
    session: Session,
    *,
    tenant_id: str,
    signature: str,
) -> None:
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    digest = hashlib.sha256(f"{tenant_id}:{signature}".encode("utf-8")).digest()
    lock_key = int.from_bytes(digest[:8], byteorder="big", signed=True)
    session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": lock_key},
    )


def _campaign_has_source_plan(
    session: Session,
    *,
    tenant_id: str,
    campaign_id: str,
    exclude_plan_id: str | None = None,
) -> bool:
    statement = select(RrugcSourcePlanModel.id).where(
        RrugcSourcePlanModel.tenant_id == tenant_id,
        RrugcSourcePlanModel.campaign_id == campaign_id,
    )
    if exclude_plan_id:
        statement = statement.where(RrugcSourcePlanModel.id != exclude_plan_id)
    return session.scalar(statement.limit(1)) is not None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _retire_source_campaign_if_unreferenced(
    session: Session,
    *,
    tenant_id: str,
    campaign_id: str | None,
) -> None:
    if not campaign_id or _campaign_has_source_plan(
        session,
        tenant_id=tenant_id,
        campaign_id=campaign_id,
    ):
        return
    campaign = session.scalar(
        select(RrugcCampaignModel).where(
            RrugcCampaignModel.tenant_id == tenant_id,
            RrugcCampaignModel.id == campaign_id,
        )
    )
    if campaign is None:
        return
    campaign.auto_scout = False
    campaign.scan_next_at = None
    now = datetime.now(timezone.utc)
    lease_expires_at = _as_utc(campaign.scan_lease_expires_at)
    active_lease = (
        lease_expires_at is not None
        and lease_expires_at > now
    )
    if not active_lease:
        campaign.status = "archived"


def _source_campaign_progress(
    service: RrugcService,
    campaign: RrugcCampaignModel,
) -> int:
    counts = service.repository.campaign_usable_counts(
        campaign.tenant_id,
        campaign.id,
    )
    return int(
        counts.get("drive_ready", 0)
        if campaign.auto_import
        else counts.get("approved", 0)
    )


def _ensure_source_campaign_capacity(
    service: RrugcService,
    campaign: RrugcCampaignModel,
) -> int:
    campaign.target_count = RRUGC_SOURCE_TARGET_COUNT
    progress = _source_campaign_progress(service, campaign)
    if progress >= RRUGC_SOURCE_TARGET_COUNT:
        campaign.status = "completed"
        campaign.scout_status = "ready"
        campaign.scan_next_at = None
    else:
        campaign.status = "running"
        campaign.auto_scout = True
        campaign.completed_at = None
        # Do not erase a future retry/backoff chosen by Scout automation.
        # Capacity is re-armed by candidate state changes; UI/source-plan
        # reconciliation should only initialize scheduling when none exists.
        if campaign.scan_lease_expires_at is None and campaign.scan_next_at is None:
            campaign.scan_next_at = datetime.now(timezone.utc)
    return progress


def _reconcile_embroidery_groups(
    session: Session,
    *,
    tenant_id: str,
    root_folder_id: str,
) -> None:
    plans = list(
        session.scalars(
            select(RrugcSourcePlanModel).where(
                RrugcSourcePlanModel.tenant_id == tenant_id,
                RrugcSourcePlanModel.root_folder_id == root_folder_id,
                RrugcSourcePlanModel.status == "ready",
            )
        )
    )
    groups: dict[str, list[RrugcSourcePlanModel]] = defaultdict(list)
    service = RrugcService(session)
    campaign_cache: dict[str, RrugcCampaignModel] = {}
    for plan in plans:
        if plan.campaign_id:
            campaign = campaign_cache.get(plan.campaign_id)
            if campaign is None:
                campaign = session.scalar(
                    select(RrugcCampaignModel).where(
                        RrugcCampaignModel.tenant_id == tenant_id,
                        RrugcCampaignModel.id == plan.campaign_id,
                    )
                )
                if campaign is not None:
                    campaign_cache[campaign.id] = campaign
            if campaign is not None:
                _ensure_source_campaign_capacity(service, campaign)
                plan.target_count = RRUGC_SOURCE_TARGET_COUNT

        if not plan.embroidery_signature and isinstance(plan.visual_context_json, dict):
            plan.embroidery_signature = embroidery_signature(plan.visual_context_json)
        if plan.embroidery_signature:
            groups[plan.embroidery_signature].append(plan)

    for signature, members in groups.items():
        campaign_ids = sorted({
            str(plan.campaign_id)
            for plan in members
            if plan.campaign_id
        })
        if not campaign_ids:
            continue

        campaigns = list(
            session.scalars(
                select(RrugcCampaignModel).where(
                    RrugcCampaignModel.tenant_id == tenant_id,
                    RrugcCampaignModel.id.in_(campaign_ids),
                )
            )
        )
        if not campaigns:
            continue

        _lock_embroidery_group(
            session,
            tenant_id=tenant_id,
            signature=signature,
        )
        active_now = datetime.now(timezone.utc)
        campaigns.sort(
            key=lambda campaign: (
                -int(
                    (_as_utc(campaign.scan_lease_expires_at) or active_now)
                    > active_now
                    if campaign.scan_lease_expires_at is not None
                    else False
                ),
                -_source_campaign_progress(service, campaign),
                campaign.created_at,
                campaign.id,
            )
        )
        canonical = campaigns[0]
        _ensure_source_campaign_capacity(service, canonical)

        retired_ids: set[str] = set()
        for plan in members:
            if plan.campaign_id and plan.campaign_id != canonical.id:
                retired_ids.add(plan.campaign_id)
            plan.campaign_id = canonical.id
            plan.target_count = RRUGC_SOURCE_TARGET_COUNT
        session.flush()

        for campaign_id in retired_ids:
            _retire_source_campaign_if_unreferenced(
                session,
                tenant_id=tenant_id,
                campaign_id=campaign_id,
            )


async def sync_source_plans(
    session: Session,
    *,
    tenant_id: str,
    user_id: str,
    settings: Settings | None = None,
    root_folder_id: str = RRUGC_SOURCE_ROOT_FOLDER_ID,
    storage: GoogleDriveAssetStorage | None = None,
    drive_client_factory: Callable[[str], GoogleDriveClient] = GoogleDriveClient,
) -> SourcePlanSyncResult:
    settings = settings or get_settings()
    storage_provider = storage or build_managed_storage_provider(settings)
    if not isinstance(storage_provider, GoogleDriveAssetStorage):
        raise RrugcSourcePlanError(
            "managed_storage_unavailable",
            "Managed Google Drive is required to scan the Realistic Review UGC source folder.",
            status_code=503,
        )

    try:
        access_token = await storage_provider.get_access_token()
        async with drive_client_factory(access_token) as drive:
            images, folders_scanned = await discover_source_images(
                drive,
                root_folder_id=root_folder_id,
            )
    except RrugcSourcePlanError:
        raise
    except Exception as exc:
        raise RrugcSourcePlanError(
            "rrugc_source_scan_failed",
            "The Realistic Review UGC source folder could not be scanned.",
            status_code=503,
        ) from exc

    processing = ProcessingRepository(session)
    created = 0
    updated = 0
    missing = 0
    queued = 0
    unchanged = 0
    seen_file_ids = {image.file_id for image in images}

    for image in images:
        plan = _get_source_plan(
            session,
            tenant_id=tenant_id,
            root_folder_id=root_folder_id,
            source_file_id=image.file_id,
        )
        is_new = plan is None
        previous_revision = plan.source_revision if plan is not None else None
        existing_visual_context = (
            dict(plan.visual_context_json or {})
            if plan is not None and isinstance(plan.visual_context_json, dict)
            else {}
        )
        requires_context_upgrade = (
            plan is not None
            and plan.status == "ready"
            and bool(existing_visual_context)
            and str(existing_visual_context.get("version") or "") != PRODUCT_VISUAL_CONTEXT_VERSION
        )
        if plan is None:
            plan = RrugcSourcePlanModel(
                tenant_id=tenant_id,
                root_folder_id=root_folder_id,
                source_file_id=image.file_id,
                source_parent_folder_id=image.parent_folder_id,
                source_relative_path=image.relative_path,
                source_name=image.name,
                source_mime_type=image.mime_type,
                source_size_bytes=image.size_bytes,
                source_width=image.width,
                source_height=image.height,
                source_modified_at=image.modified_at,
                source_web_url=image.web_url,
                source_revision=image.revision,
                analysis_revision=1,
                target_count=RRUGC_SOURCE_TARGET_COUNT,
                status="queued",
                created_by_user_id=user_id,
            )
            session.add(plan)
            session.flush()
            created += 1
        else:
            plan.source_parent_folder_id = image.parent_folder_id
            plan.source_relative_path = image.relative_path
            plan.source_name = image.name
            plan.source_mime_type = image.mime_type
            plan.source_size_bytes = image.size_bytes
            plan.source_width = image.width
            plan.source_height = image.height
            plan.source_modified_at = image.modified_at
            plan.source_web_url = image.web_url
            plan.target_count = RRUGC_SOURCE_TARGET_COUNT
            if previous_revision != image.revision:
                plan.source_revision = image.revision
                plan.analysis_revision = max(1, int(plan.analysis_revision or 0) + 1)
                plan.status = "queued"
                plan.last_error_code = None
                plan.visual_context_json = None
                plan.embroidery_signature = None
                plan.analyzed_at = None
                updated += 1
            elif requires_context_upgrade:
                plan.analysis_revision = max(1, int(plan.analysis_revision or 0) + 1)
                plan.status = "queued"
                plan.last_error_code = None
                plan.visual_context_json = None
                plan.embroidery_signature = None
                plan.analyzed_at = None
                updated += 1

        should_queue = (
            is_new
            or previous_revision != image.revision
            or not plan.campaign_id
            or plan.status in {"failed", "missing", "retry", "queued"}
        )
        if not should_queue:
            unchanged += 1
            continue

        if (
            not is_new
            and previous_revision == image.revision
            and plan.status in {"failed", "missing"}
        ):
            plan.analysis_revision = max(1, int(plan.analysis_revision or 0) + 1)
            plan.status = "queued"
            plan.last_error_code = None

        job, was_created = processing.create_job_once(
            tenant_id=tenant_id,
            job_type=RRUGC_SOURCE_PLAN_JOB_TYPE,
            entity_type="rrugc_source_plan",
            entity_id=plan.id,
            idempotency_key=(
                f"rrugc-source-plan-analyze:{plan.id}:{plan.source_revision}:"
                f"{plan.analysis_revision}"
            ),
            payload={
                "source_plan_id": plan.id,
                "source_revision": plan.source_revision,
                "analysis_revision": plan.analysis_revision,
            },
            provider_key="gemini",
            provider_scope="ai",
            max_attempts=5,
            # RRUGC shares Image Gemini capacity and must remain background work.
            priority=0,
        )
        if was_created:
            queued += 1
            plan.status = "queued"
        else:
            unchanged += 1
            if job.status in {"failed"}:
                plan.status = "failed"

    existing_plans = list(
        session.scalars(
            select(RrugcSourcePlanModel).where(
                RrugcSourcePlanModel.tenant_id == tenant_id,
                RrugcSourcePlanModel.root_folder_id == root_folder_id,
            )
        )
    )
    for plan in existing_plans:
        if plan.source_file_id in seen_file_ids:
            continue

        campaign_id = plan.campaign_id
        session.execute(
            delete(ProcessingJobModel).where(
                ProcessingJobModel.tenant_id == tenant_id,
                ProcessingJobModel.entity_type == "rrugc_source_plan",
                ProcessingJobModel.entity_id == plan.id,
            )
        )
        session.delete(plan)
        session.flush()
        missing += 1
        _retire_source_campaign_if_unreferenced(
            session,
            tenant_id=tenant_id,
            campaign_id=campaign_id,
        )

    _reconcile_embroidery_groups(
        session,
        tenant_id=tenant_id,
        root_folder_id=root_folder_id,
    )

    session.commit()
    return SourcePlanSyncResult(
        root_folder_id=root_folder_id,
        folders_scanned=folders_scanned,
        images_found=len(images),
        plans_created=created,
        plans_updated=updated,
        plans_missing=missing,
        jobs_queued=queued,
        unchanged=unchanged,
    )


def _synthetic_product_snapshot(plan: RrugcSourcePlanModel) -> dict[str, Any]:
    folder = str(PurePosixPath(plan.source_relative_path).parent)
    folder_note = "" if folder in {"", "."} else f" Source folder: {folder}."
    return {
        "id": f"rrugc-source:{plan.source_file_id}",
        "revision": int(plan.analysis_revision or 0),
        "name": PurePosixPath(plan.source_name).stem[:200] or plan.source_name[:200],
        "product_type": "cap",
        "source_category": "embroidered hat",
        "source_description": (
            "Source image for a personalized embroidered hat."
            + folder_note
            + " Infer Pinterest lifestyle context primarily from the visible embroidery/artwork."
        )[:1000],
    }


def _synthetic_reference_snapshot(plan: RrugcSourcePlanModel) -> list[dict[str, Any]]:
    return [
        {
            "id": plan.source_file_id,
            "variant_id": None,
            "view_type": "embroidery_closeup",
            "version": int(plan.analysis_revision or 0),
            "content_hash": plan.source_revision,
            "remote_file_id": plan.source_file_id,
        }
    ]


def _source_campaign_name(plan: RrugcSourcePlanModel) -> str:
    path = plan.source_relative_path.strip() or plan.source_name
    value = f"Pinterest refs — {path}"
    return value[:200]


def _search_anchors(profile: dict[str, Any]) -> list[str]:
    contextual = product_context_search_queries(profile)
    direct = [query for query, level in contextual if level == "direct"]
    adjacent = [query for query, level in contextual if level == "adjacent"]
    generic = [query for query, level in contextual if level == "generic"]
    rows = list(dict.fromkeys([*direct, *adjacent, *generic]))
    return rows[:2] or ["casual lifestyle candid phone photo"]


class RrugcSourcePlanAnalyzeJobHandler:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def __call__(
        self,
        context: JobHandlerContext,
    ) -> JobHandlerResult | DeferredJobOutcome:
        try:
            return asyncio.run(self._execute(context))
        except AiProviderUnavailableError:
            self._mark_error(context, "ai_provider_unavailable", terminal=True)
            return JobHandlerResult.non_retryable(
                "ai_provider_unavailable",
                "Embroidery context analyzer is unavailable.",
            )
        except AiProviderError as exc:
            retry_at = getattr(exc, "earliest_retry_at", None)
            self._mark_error(context, exc.code, terminal=not exc.retryable)
            if exc.retryable and isinstance(retry_at, datetime):
                return DeferredJobOutcome(
                    exc.code,
                    "Embroidery context analyzer is temporarily unavailable.",
                    retry_at,
                )
            outcome = (
                JobHandlerResult.retryable
                if exc.retryable
                else JobHandlerResult.non_retryable
            )
            return outcome(exc.code, "Embroidery context analysis failed.")
        except StorageProviderError as exc:
            self._mark_error(context, "rrugc_source_image_unavailable", terminal=not exc.retryable)
            outcome = (
                JobHandlerResult.retryable
                if exc.retryable
                else JobHandlerResult.non_retryable
            )
            return outcome(
                "rrugc_source_image_unavailable",
                "The source embroidery image could not be read from Google Drive.",
            )
        except RrugcSourcePlanError as exc:
            self._mark_error(context, exc.code, terminal=exc.status_code < 500)
            outcome = (
                JobHandlerResult.retryable
                if exc.status_code >= 500
                else JobHandlerResult.non_retryable
            )
            return outcome(exc.code, exc.message)
        except Exception:
            context.logger.exception(
                "rrugc_source_plan_analysis_failed",
                extra={
                    "source_plan_id": context.job.entity_id,
                    "tenant_id": context.job.tenant_id,
                },
            )
            self._mark_error(context, "rrugc_source_plan_internal_error", terminal=False)
            return JobHandlerResult.retryable(
                "rrugc_source_plan_internal_error",
                "Source plan analysis failed.",
            )

    async def _execute(self, context: JobHandlerContext) -> JobHandlerResult:
        plan_id = str(context.job.payload.get("source_plan_id") or "")
        revision = str(context.job.payload.get("source_revision") or "")
        analysis_revision = context.job.payload.get("analysis_revision")
        if (
            not plan_id
            or plan_id != context.job.entity_id
            or not revision
            or not isinstance(analysis_revision, int)
        ):
            return JobHandlerResult.non_retryable(
                "rrugc_source_plan_job_invalid",
                "Source plan job payload is invalid.",
            )
        if context.is_cancelled:
            return JobHandlerResult.cancelled()

        with context.dependencies.session_factory() as session:
            plan = session.scalar(
                select(RrugcSourcePlanModel).where(
                    RrugcSourcePlanModel.tenant_id == context.job.tenant_id,
                    RrugcSourcePlanModel.id == plan_id,
                )
            )
            if plan is None:
                return JobHandlerResult.non_retryable(
                    "rrugc_source_plan_not_found",
                    "Source plan was not found.",
                )
            if (
                plan.source_revision != revision
                or plan.analysis_revision != analysis_revision
                or plan.status == "missing"
            ):
                return JobHandlerResult.completed()
            plan.status = "analyzing"
            plan.last_error_code = None
            session.commit()
            source_file_id = plan.source_file_id
            source_mime_type = plan.source_mime_type
            source_size_bytes = plan.source_size_bytes
            source_width = plan.source_width
            source_height = plan.source_height

        settings = self.settings or get_settings()
        storage = build_managed_storage_provider(settings)
        if not isinstance(storage, GoogleDriveAssetStorage):
            raise RrugcSourcePlanError(
                "managed_storage_unavailable",
                "Managed Google Drive is required for embroidery source analysis.",
                status_code=503,
            )

        stream = await storage.open_asset(
            OpenStoredAssetInput(
                tenant_id=context.job.tenant_id,
                asset_id=plan_id,
                remote_file_id=source_file_id,
                content_type=source_mime_type,
                size_bytes=source_size_bytes,
            )
        )
        chunks: list[bytes] = []
        total = 0
        try:
            async for chunk in stream.body:
                total += len(chunk)
                if total > RRUGC_SOURCE_MAX_IMAGE_BYTES:
                    raise RrugcSourcePlanError(
                        "rrugc_source_image_too_large",
                        "The source embroidery image is too large to analyze.",
                        status_code=409,
                    )
                chunks.append(chunk)
        finally:
            await stream.close()

        if context.is_cancelled:
            return JobHandlerResult.cancelled()

        gemini_slot = scheduled_rrugc_gemini_slot(context.job)
        registry = build_ai_provider_registry(
            settings,
            session_factory=context.dependencies.session_factory,
        )
        try:
            provider = registry.require("gemini")
            with context.dependencies.session_factory() as session:
                plan = session.scalar(
                    select(RrugcSourcePlanModel).where(
                        RrugcSourcePlanModel.tenant_id == context.job.tenant_id,
                        RrugcSourcePlanModel.id == plan_id,
                    )
                )
                if (
                    plan is None
                    or plan.source_revision != revision
                    or plan.analysis_revision != analysis_revision
                    or plan.status == "missing"
                ):
                    return JobHandlerResult.completed()
                product_snapshot = _synthetic_product_snapshot(plan)
                reference_snapshot = _synthetic_reference_snapshot(plan)

            document, provider_name, model = await analyze_product_visual_reference(
                provider=provider,
                tenant_id=context.job.tenant_id,
                reference_id=plan_id,
                image_bytes=b"".join(chunks),
                image_mime_type=stream.content_type or source_mime_type,
                width=source_width,
                height=source_height,
                product_snapshot=product_snapshot,
                view_type="embroidery_closeup",
                preferred_model=(gemini_slot.model if gemini_slot else None),
                preferred_credential_provider=(
                    gemini_slot.credential_provider if gemini_slot else None
                ),
            )
        finally:
            await registry.aclose()

        visual_context = merge_product_visual_context(
            [(document, reference_snapshot[0], provider_name, model)],
            binding_fingerprint=product_visual_binding_fingerprint(
                product_snapshot,
                reference_snapshot,
            ),
            analyzed_at=datetime.now(timezone.utc).isoformat(),
        )
        profile = derive_product_context_profile(
            product_snapshot=product_snapshot,
            campaign_name=product_snapshot["name"],
            config={
                "auto_context": True,
                "visual_context": visual_context,
            },
            reference_snapshot=reference_snapshot,
        )
        anchors = _search_anchors(profile)
        queries = build_campaign_search_queries(
            name=product_snapshot["name"],
            queries=anchors,
            protected_queries=anchors,
            product_snapshot=product_snapshot,
            discovery_mode="product_context",
            product_context=profile,
            max_queries=20,
        )

        with context.dependencies.session_factory() as session:
            plan = session.scalar(
                select(RrugcSourcePlanModel).where(
                    RrugcSourcePlanModel.tenant_id == context.job.tenant_id,
                    RrugcSourcePlanModel.id == plan_id,
                )
            )
            if (
                plan is None
                or plan.source_revision != revision
                or plan.analysis_revision != analysis_revision
                or plan.status == "missing"
            ):
                return JobHandlerResult.completed()

            signature = embroidery_signature(visual_context)
            old_campaign_id = plan.campaign_id
            plan.embroidery_signature = signature
            if signature:
                _lock_embroidery_group(
                    session,
                    tenant_id=context.job.tenant_id,
                    signature=signature,
                )

            shared_plan = None
            if signature:
                shared_plan = session.scalar(
                    select(RrugcSourcePlanModel)
                    .where(
                        RrugcSourcePlanModel.tenant_id == context.job.tenant_id,
                        RrugcSourcePlanModel.embroidery_signature == signature,
                        RrugcSourcePlanModel.id != plan.id,
                        RrugcSourcePlanModel.campaign_id.is_not(None),
                        RrugcSourcePlanModel.status == "ready",
                    )
                    .order_by(
                        RrugcSourcePlanModel.created_at.asc(),
                        RrugcSourcePlanModel.id.asc(),
                    )
                    .limit(1)
                )

            service = RrugcService(session)
            campaign: RrugcCampaignModel | None = None
            shared_campaign = False
            if shared_plan is not None and shared_plan.campaign_id:
                campaign = session.scalar(
                    select(RrugcCampaignModel).where(
                        RrugcCampaignModel.tenant_id == context.job.tenant_id,
                        RrugcCampaignModel.id == shared_plan.campaign_id,
                    )
                )
                if campaign is not None:
                    shared_campaign = True
                    plan.campaign_id = campaign.id

            if campaign is None and old_campaign_id:
                old_campaign = session.scalar(
                    select(RrugcCampaignModel).where(
                        RrugcCampaignModel.tenant_id == context.job.tenant_id,
                        RrugcCampaignModel.id == old_campaign_id,
                    )
                )
                if (
                    old_campaign is not None
                    and not _campaign_has_source_plan(
                        session,
                        tenant_id=context.job.tenant_id,
                        campaign_id=old_campaign.id,
                        exclude_plan_id=plan.id,
                    )
                ):
                    campaign = old_campaign
                    plan.campaign_id = campaign.id

            if campaign is None:
                campaign, _token = service.create_campaign(
                    tenant_id=context.job.tenant_id,
                    user_id=plan.created_by_user_id,
                    name=_source_campaign_name(plan),
                    query=anchors[0],
                    search_queries=anchors,
                    target_count=RRUGC_SOURCE_TARGET_COUNT,
                    max_scroll_batches=5,
                    auto_import=True,
                    discovery_mode="keyword",
                    auto_scout=False,
                    commit=False,
                )
                plan.campaign_id = campaign.id

            if shared_campaign:
                _ensure_source_campaign_capacity(service, campaign)
            else:
                campaign.name = _source_campaign_name(plan)
                campaign.discovery_mode = "product_context"
                campaign.product_snapshot_json = product_snapshot
                campaign.product_reference_snapshot_json = reference_snapshot
                campaign.product_context_json = profile
                campaign.target_count = RRUGC_SOURCE_TARGET_COUNT
                campaign.query = queries[0]
                campaign.search_queries_json = queries
                campaign.search_query_anchors_json = anchors
                campaign.auto_import = True
                campaign.auto_scout = True
                campaign.scan_next_at = datetime.now(timezone.utc)
                campaign.scan_empty_streak = 0
                campaign.scan_failure_streak = 0
                campaign.scan_last_error_code = None
                campaign.status = "running"
                campaign.completed_at = None
                service.refresh_campaign_discovery(campaign, commit=False)

            plan.visual_context_json = visual_context
            plan.target_count = RRUGC_SOURCE_TARGET_COUNT
            plan.status = "ready"
            plan.last_error_code = None
            plan.analyzed_at = datetime.now(timezone.utc)
            session.flush()

            if old_campaign_id and old_campaign_id != plan.campaign_id:
                _retire_source_campaign_if_unreferenced(
                    session,
                    tenant_id=context.job.tenant_id,
                    campaign_id=old_campaign_id,
                )
            session.commit()

        return JobHandlerResult.completed()

    @staticmethod
    def _mark_error(
        context: JobHandlerContext,
        code: str,
        *,
        terminal: bool,
    ) -> None:
        with context.dependencies.session_factory() as session:
            plan = session.scalar(
                select(RrugcSourcePlanModel).where(
                    RrugcSourcePlanModel.tenant_id == context.job.tenant_id,
                    RrugcSourcePlanModel.id == context.job.entity_id,
                )
            )
            if plan is None:
                return
            plan.status = "failed" if terminal else "retry"
            plan.last_error_code = code
            session.commit()
