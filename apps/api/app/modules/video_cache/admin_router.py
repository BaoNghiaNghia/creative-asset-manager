from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.modules.authorization.principal import CurrentPrincipal, require_platform_admin
from app.modules.video_cache.guard import VIDEO_DELIVERY_GUARD
from app.modules.video_cache.metrics import delivery_observability_snapshot
from app.modules.video_cache.runtime import (
    VideoDeliveryPrerequisiteError,
    VideoDeliveryRuntimeService,
    VideoDeliveryRuntimeUnavailable,
)

router = APIRouter(
    prefix="/api/v1/admin/video-delivery",
    tags=["video-delivery-admin"],
)


class VideoDeliveryPrerequisitesResponse(BaseModel):
    r2_video_cache_enabled: bool
    delivery_configured: bool
    rollout_scope_configured: bool


class VideoDeliveryRuntimeResponse(BaseModel):
    setting: str
    runtime_enabled: bool
    effective_enabled: bool
    can_enable: bool
    prerequisites: VideoDeliveryPrerequisitesResponse
    blockers: list[str]
    rollout_mode: str
    canary_tenant_count: int
    updated_at: str | None


class VideoDeliveryRuntimeUpdate(BaseModel):
    enabled: bool
    reason: str = Field(min_length=3, max_length=500)


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "video_delivery_runtime_unavailable",
            "message": "Video delivery runtime configuration is unavailable.",
        },
    )


@router.get("/runtime", response_model=VideoDeliveryRuntimeResponse)
def get_video_delivery_runtime(
    request: Request,
    _principal: CurrentPrincipal = Depends(require_platform_admin),
):
    try:
        with SessionLocal() as session:
            return VideoDeliveryRuntimeService(
                session, _settings(request)
            ).get_status()
    except (VideoDeliveryRuntimeUnavailable, SQLAlchemyError) as exc:
        raise _unavailable() from exc


@router.put("/runtime", response_model=VideoDeliveryRuntimeResponse)
def update_video_delivery_runtime(
    body: VideoDeliveryRuntimeUpdate,
    request: Request,
    principal: CurrentPrincipal = Depends(require_platform_admin),
):
    with SessionLocal() as session:
        try:
            result = VideoDeliveryRuntimeService(
                session, _settings(request)
            ).set_enabled(
                body.enabled,
                actor_id=principal.user_id,
                reason=body.reason,
            )
            session.commit()
            return result
        except VideoDeliveryPrerequisiteError as exc:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "video_delivery_prerequisites_unavailable",
                    "message": "Video CDN delivery cannot be enabled until all server prerequisites are ready.",
                },
            ) from exc
        except ValueError as exc:
            session.rollback()
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_request", "message": str(exc)},
            ) from exc
        except (VideoDeliveryRuntimeUnavailable, SQLAlchemyError) as exc:
            session.rollback()
            raise _unavailable() from exc


@router.get("/observability")
def get_video_delivery_observability(
    request: Request,
    _principal: CurrentPrincipal = Depends(require_platform_admin),
):
    settings = _settings(request)
    return {
        "metrics": delivery_observability_snapshot(),
        "guard": VIDEO_DELIVERY_GUARD.snapshot(settings),
    }
