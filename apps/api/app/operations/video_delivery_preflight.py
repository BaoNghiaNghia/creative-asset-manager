"""Fail-closed Phase 4C preflight for R2 Public Review video delivery.

The preflight is read-only. It never enables runtime delivery, mutates cache
metadata, uploads R2 objects, or prints credentials/signed URLs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import (
    SessionLocal,
    engine,
    resolve_database_url,
    validate_alembic_head,
)
from app.modules.video_cache.delivery import (
    VideoCacheDeliveryService,
    VideoDeliveryError,
)
from app.modules.video_cache.model import VideoCacheObjectModel
from app.modules.video_cache.quota import VideoCacheQuota
from app.modules.video_cache.runtime import (
    VideoDeliveryRuntimeService,
    VideoDeliveryRuntimeUnavailable,
)
from app.modules.video_cache.service import video_cache_key


@dataclass(frozen=True)
class PreflightCheck:
    code: str
    ok: bool
    detail: str


def _check(code: str, ok: bool, passed: str, failed: str) -> PreflightCheck:
    return PreflightCheck(code=code, ok=ok, detail=passed if ok else failed)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_no_redirect(request: Request, *, timeout: float):
    return build_opener(_NoRedirect).open(request, timeout=timeout)


def video_delivery_preflight(
    session: Session,
    settings: Settings,
    *,
    require_production: bool = False,
    require_runtime_off: bool = True,
    require_ready: bool = True,
    require_canary_scope: bool = True,
) -> list[PreflightCheck]:
    """Return safe, credential-free rollout checks using only local config/DB state."""
    checks: list[PreflightCheck] = []
    checks.append(_check(
        "environment",
        not require_production or settings.is_production,
        "environment accepted",
        "production environment is required",
    ))
    checks.append(_check(
        "r2_cache_enabled",
        bool(settings.R2_VIDEO_CACHE_ENABLED),
        "R2 video cache is enabled",
        "R2 video cache is disabled",
    ))
    checks.append(_check(
        "delivery_configured",
        bool(settings.video_delivery_configured),
        "signed video delivery is configured",
        "signed video delivery configuration is incomplete",
    ))
    checks.append(_check(
        "delivery_guard_enabled",
        bool(settings.VIDEO_CDN_DELIVERY_GUARD_ENABLED),
        "process-local video delivery guard is enabled",
        "video delivery guard must be enabled before canary rollout",
    ))

    origin_kind = settings.video_media_origin_kind
    approved_origin = bool(settings.video_media_origin_approved)
    checks.append(_check(
        "approved_media_origin",
        approved_origin,
        "media origin passed the signed-delivery origin policy",
        (
            "media origin must be a custom HTTPS origin, or an explicitly opted-in "
            "workers.dev Worker; r2.dev and raw R2 endpoints are never allowed"
        ),
    ))
    if origin_kind == "workers_dev":
        checks.append(_check(
            "workers_dev_operator_opt_in",
            bool(settings.R2_VIDEO_MEDIA_ALLOW_WORKERS_DEV),
            "workers.dev media delivery is enabled under explicit operator opt-in",
            "workers.dev media delivery requires R2_VIDEO_MEDIA_ALLOW_WORKERS_DEV=true",
        ))
    if require_canary_scope:
        checks.append(_check(
            "canary_rollout_scope",
            settings.video_delivery_rollout_mode == "canary",
            "video delivery is limited to an explicit canary tenant set",
            "video delivery must use a non-empty canary tenant set before rollout",
        ))

    try:
        runtime = VideoDeliveryRuntimeService(session, settings).get_status()
        runtime_present = True
    except VideoDeliveryRuntimeUnavailable:
        runtime = None
        runtime_present = False
    checks.append(_check(
        "runtime_row_present",
        runtime_present,
        "persisted runtime gate is available",
        "persisted runtime gate is unavailable",
    ))
    if runtime_present and runtime is not None:
        checks.append(_check(
            "runtime_prerequisites",
            bool(runtime["can_enable"]),
            "runtime prerequisites are ready",
            "runtime prerequisites are not ready",
        ))
        if require_runtime_off:
            checks.append(_check(
                "runtime_off_before_rollout",
                not bool(runtime["runtime_enabled"]),
                "runtime delivery remains disabled for staged rollout",
                "runtime delivery must be disabled before rollout preflight",
            ))

    usage = VideoCacheQuota.usage(session)
    checks.append(_check(
        "quota_headroom",
        usage.effective_bytes <= settings.R2_VIDEO_CACHE_SOFT_LIMIT_BYTES,
        "effective cache bytes are within the soft limit",
        "effective cache bytes exceed the soft limit",
    ))
    deleting_objects = int(session.scalar(
        select(func.count(VideoCacheObjectModel.id)).where(
            VideoCacheObjectModel.status == "deleting"
        )
    ) or 0)
    checks.append(_check(
        "no_deleting_objects",
        deleting_objects == 0,
        "no cache deletion is currently in progress",
        "cache deletion is in progress",
    ))
    if require_ready:
        ready_objects = int(session.scalar(
            select(func.count(VideoCacheObjectModel.id)).where(
                VideoCacheObjectModel.status == "ready"
            )
        ) or 0)
        checks.append(_check(
            "ready_video_available",
            ready_objects > 0,
            "at least one READY video exists for canary validation",
            "no READY video is available for canary validation",
        ))
    return checks


def probe_worker_ticket(
    settings: Settings,
    *,
    opener=_open_no_redirect,
    timeout_seconds: float = 5.0,
) -> PreflightCheck:
    """Verify route + HMAC agreement with a signed HEAD for a random missing key.

    A correctly configured Worker authenticates the ticket, checks private R2,
    and returns 404 for the random key. The signed URL itself is never returned
    or printed.
    """
    if not settings.video_delivery_configured:
        return PreflightCheck(
            code="worker_signed_head_probe",
            ok=False,
            detail="signed video delivery configuration is incomplete",
        )

    content_hash = hashlib.sha256(uuid4().bytes).hexdigest()
    synthetic = VideoCacheObjectModel(
        tenant_id="cam-preflight",
        asset_id="00000000-0000-0000-0000-000000000000",
        source_asset_id="00000000-0000-0000-0000-000000000000",
        content_hash=content_hash,
        r2_key=video_cache_key("cam-preflight", content_hash),
        mime_type="video/mp4",
        status="ready",
        size_bytes=1,
    )
    try:
        ticket = VideoCacheDeliveryService(settings).create_signed_url(synthetic)
        request = Request(
            ticket.url,
            method="HEAD",
            headers={
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "User-Agent": "creative-asset-manager-preflight",
                "Accept": "*/*",
            },
        )
        try:
            response = opener(request, timeout=timeout_seconds)
        except HTTPError as exc:
            status = exc.code if isinstance(exc.code, int) else "unknown"
            if status == 404:
                return PreflightCheck(
                    code="worker_signed_head_probe",
                    ok=True,
                    detail="Worker accepted the signed ticket and confirmed the random key is absent",
                )
            return PreflightCheck(
                code="worker_signed_head_probe",
                ok=False,
                detail=f"Worker returned HTTP {status} for the signed HEAD probe",
            )
        except (URLError, TimeoutError, OSError):
            return PreflightCheck(
                code="worker_signed_head_probe",
                ok=False,
                detail="Worker signed probe could not reach the configured media origin",
            )
        try:
            status = getattr(response, "status", None)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        return PreflightCheck(
            code="worker_signed_head_probe",
            ok=False,
            detail=(
                f"Worker returned HTTP {status} for the random preflight key"
                if isinstance(status, int)
                else "Worker returned an unexpected probe response"
            ),
        )
    except VideoDeliveryError:
        return PreflightCheck(
            code="worker_signed_head_probe",
            ok=False,
            detail="Worker probe ticket could not be created safely",
        )



def probe_ready_video_ticket(
    session: Session,
    settings: Settings,
    *,
    opener=_open_no_redirect,
    timeout_seconds: float = 5.0,
) -> PreflightCheck:
    """HEAD one existing READY object through the signed Worker path.

    No body is downloaded and no tenant, asset, key, URL, ETag, or secret is
    returned in the operator result.
    """
    row = session.scalar(
        select(VideoCacheObjectModel)
        .where(VideoCacheObjectModel.status == "ready")
        .order_by(VideoCacheObjectModel.cached_at.desc(), VideoCacheObjectModel.id)
        .limit(1)
    )
    if row is None:
        return PreflightCheck(
            code="worker_ready_head_probe",
            ok=False,
            detail="no READY video is available for the Worker canary probe",
        )
    try:
        ticket = VideoCacheDeliveryService(settings).create_signed_url(row)
        request = Request(
            ticket.url,
            method="HEAD",
            headers={
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "User-Agent": "creative-asset-manager-preflight",
                "Accept": "*/*",
            },
        )
        try:
            response = opener(request, timeout=timeout_seconds)
        except HTTPError:
            return PreflightCheck(
                code="worker_ready_head_probe",
                ok=False,
                detail="Worker could not serve the READY canary object",
            )
        except (URLError, TimeoutError, OSError):
            return PreflightCheck(
                code="worker_ready_head_probe",
                ok=False,
                detail="Worker READY canary probe could not reach the configured media origin",
            )
        try:
            status = getattr(response, "status", None)
            headers = getattr(response, "headers", {})
            content_length = headers.get("Content-Length") if headers is not None else None
            content_type = headers.get("Content-Type") if headers is not None else None
            accept_ranges = headers.get("Accept-Ranges") if headers is not None else None
            ok = (
                status == 200
                and content_length == str(row.size_bytes)
                and isinstance(content_type, str)
                and content_type.startswith("video/")
                and accept_ranges == "bytes"
            )
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        return PreflightCheck(
            code="worker_ready_head_probe",
            ok=ok,
            detail=(
                "Worker served matching metadata for a READY canary object"
                if ok
                else "Worker READY canary metadata did not match the durable ledger"
            ),
        )
    except VideoDeliveryError:
        return PreflightCheck(
            code="worker_ready_head_probe",
            ok=False,
            detail="READY canary ticket could not be created safely",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Phase 4C R2 video delivery rollout preflight"
    )
    parser.add_argument(
        "--require-production",
        action="store_true",
        help="fail unless APP_ENV=production",
    )
    parser.add_argument(
        "--allow-runtime-enabled",
        action="store_true",
        help="do not require VIDEO_CDN_DELIVERY_ENABLED to remain OFF",
    )
    parser.add_argument(
        "--allow-empty-cache",
        action="store_true",
        help="do not require an existing READY video for canary validation",
    )
    parser.add_argument(
        "--probe-worker",
        action="store_true",
        help="send one signed HEAD to a random missing Worker key; performs no write",
    )
    parser.add_argument(
        "--probe-ready-object",
        action="store_true",
        help="HEAD one existing READY cache object through the Worker; downloads no body",
    )
    parser.add_argument(
        "--allow-global-rollout",
        action="store_true",
        help="allow explicit global rollout config instead of requiring tenant canary scope",
    )
    args = parser.parse_args(argv)

    settings = Settings()
    checks: list[PreflightCheck] = []
    try:
        validate_alembic_head(
            engine,
            database_url=resolve_database_url(settings),
        )
        checks.append(PreflightCheck(
            code="alembic_head",
            ok=True,
            detail="database schema is at the single expected Alembic head",
        ))
    except Exception:
        checks.append(PreflightCheck(
            code="alembic_head",
            ok=False,
            detail="database schema is unavailable or not at the expected Alembic head",
        ))

    with SessionLocal() as session:
        checks.extend(video_delivery_preflight(
            session,
            settings,
            require_production=args.require_production,
            require_runtime_off=not args.allow_runtime_enabled,
            require_ready=not args.allow_empty_cache,
            require_canary_scope=not args.allow_global_rollout,
        ))
        if args.probe_ready_object:
            checks.append(probe_ready_video_ticket(session, settings))

    if args.probe_worker:
        checks.append(probe_worker_ticket(settings))

    ready = all(item.ok for item in checks)
    print(json.dumps({
        "ready": ready,
        "checks": [asdict(item) for item in checks],
    }, sort_keys=True))
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
