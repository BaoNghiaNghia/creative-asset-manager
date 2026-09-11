from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.assets.model import AssetModel
from app.modules.processing.repository import ProcessingRepository

from .model import VideoGenerationReferenceModel, VideoGenerationRunModel
from .repository import VideoGenerationRepository

MIMES = frozenset({"image/jpeg", "image/png", "image/webp"})
MAX = 15 * 1024 * 1024
TOTAL = 30 * 1024 * 1024


class Error(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def enabled(settings, tenant_id: str) -> bool:
    canaries = {
        item.strip()
        for item in settings.VIDEO_GENERATION_CANARY_TENANT_IDS.split(",")
        if item.strip()
    }
    return bool(
        settings.PROCESSING_JOBS_ENABLED
        and settings.MANAGED_ASSET_STORAGE_ENABLED
        and settings.VIDEO_GENERATION_ENABLED
        and settings.DOLA_RENDER_GATEWAY_ENABLED
        and tenant_id in canaries
    )


def fingerprint(tenant_id: str, user_id: str, request) -> str:
    body = {
        "provider": "dola",
        "model": request.model,
        "prompt": request.prompt.strip(),
        "aspect_ratio": request.aspect_ratio,
        "duration_seconds": request.duration_seconds,
        "references": request.reference_asset_ids,
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class Service:
    def __init__(self, session: Session, settings):
        self.session = session
        self.settings = settings
        self.runs = VideoGenerationRepository(session)
        self.jobs = ProcessingRepository(session, settings)

    def create(self, tenant_id: str, user_id: str, request):
        if not enabled(self.settings, tenant_id):
            raise Error("video_generation_disabled", "Video generation is disabled.", 503)

        request_fingerprint = fingerprint(tenant_id, user_id, request)
        existing = self.runs.get_by_client_request(
            tenant_id, user_id, request.client_request_id
        )
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise Error(
                    "video_generation_request_conflict",
                    "Client request conflicts.",
                    409,
                )
            return existing

        assets = list(
            self.session.scalars(
                select(AssetModel).where(
                    AssetModel.tenant_id == tenant_id,
                    AssetModel.id.in_(request.reference_asset_ids),
                )
            )
        )
        if len(assets) != len(request.reference_asset_ids):
            raise Error("reference_asset_not_found", "Reference asset was not found.", 404)

        total = 0
        for asset in assets:
            if (asset.mime_type or "").split(";", 1)[0].lower() not in MIMES:
                raise Error(
                    "reference_asset_unsupported",
                    "Reference image type is unsupported.",
                )
            if asset.size_bytes is not None:
                total += asset.size_bytes
                if asset.size_bytes > MAX or total > TOTAL:
                    raise Error(
                        "reference_asset_too_large",
                        "Reference image exceeds size limit.",
                    )

        run = VideoGenerationRunModel(
            tenant_id=tenant_id,
            provider_model=request.model,
            prompt=request.prompt.strip(),
            aspect_ratio=request.aspect_ratio,
            duration_seconds=request.duration_seconds,
            request_fingerprint=request_fingerprint,
            client_request_id=request.client_request_id,
            created_by_user_id=user_id,
        )
        try:
            with self.session.begin_nested():
                run, created = self.runs.create_idempotent(run)
                if not created:
                    if run.request_fingerprint != request_fingerprint:
                        raise Error(
                            "video_generation_request_conflict",
                            "Client request conflicts.",
                            409,
                        )
                    return run
                for position, asset_id in enumerate(request.reference_asset_ids):
                    self.session.add(
                        VideoGenerationReferenceModel(
                            tenant_id=tenant_id,
                            run_id=run.id,
                            asset_id=asset_id,
                            position=position,
                        )
                    )
                self.session.flush()
                self.jobs.create_job(
                    tenant_id=tenant_id,
                    job_type="video_generate",
                    entity_type="video_generation_run",
                    entity_id=run.id,
                    idempotency_key=f"video-generate:{run.id}",
                    payload={"video_generation_run_id": run.id},
                    max_attempts=5,
                    provider_key="dola",
                    provider_scope="video_generation",
                )
            self.session.commit()
            return run
        except Exception:
            self.session.rollback()
            raise

    def get(self, tenant_id: str, generation_id: str):
        run = self.runs.get(tenant_id, generation_id)
        if run is None:
            raise Error(
                "video_generation_not_found",
                "Video generation was not found.",
                404,
            )
        return run
