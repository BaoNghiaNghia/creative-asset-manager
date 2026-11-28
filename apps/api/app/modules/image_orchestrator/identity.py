"""Use the existing CAM credential authorities, not another token registry.

The P2a read API grants no image generation capability. An authenticated
identity is not a renderer verification, resource grant or claim authorization.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session
from app.core.database import get_db


@dataclass(frozen=True)
class ImageIdentity:
    tenant: str
    worker: str
    kind: str


def resolve_identity(request: Request, authorization: str | None,
                     agent_id: str | None, db: Session) -> ImageIdentity:
    if not authorization:
        raise HTTPException(401, "image_authentication_required")
    if agent_id is not None:
        # An agent header selects only agent authentication; never fall back
        # to Muse if the UUID or credential is invalid.
        try:
            canonical = str(UUID(agent_id))
        except (ValueError, AttributeError):
            raise HTTPException(401, "image_authentication_required") from None
        if canonical != agent_id:
            raise HTTPException(401, "image_authentication_required")
        from app.modules.etsy_spy.service import authenticate_agent
        agent = authenticate_agent(db, canonical, authorization, image_only=True)
        return ImageIdentity(agent.tenant_id, "chatgpt-ext-" + canonical, "chatgpt_extension")
    from app.modules.muse_bridge.router import _muse, _muse_identity
    tenant = _muse(request, authorization)
    worker = _muse_identity(request)
    # Legacy shared credentials cannot identify a unique image worker.
    if worker not in ("muse-01", "muse-02"):
        raise HTTPException(403, "image_worker_identity_required")
    return ImageIdentity(tenant, worker, "muse")


def image_identity(request: Request,
                   authorization: str | None = Header(default=None),
                   x_cam_image_agent_id: str | None = Header(default=None),
                   db: Session = Depends(get_db)) -> ImageIdentity:
    # Delegate dedicated image-worker authentication to the existing Lite authority.
    # The legacy verifier remains in place for other existing image clients.
    if authorization and x_cam_image_agent_id is None:
        from .lite_api import lite_identity
        try:
            return lite_identity(request, authorization, None, db)
        except HTTPException as exc:
            if exc.status_code not in (401, 503):
                raise
    return resolve_identity(request, authorization, x_cam_image_agent_id, db)
