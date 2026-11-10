"""Provision a dedicated Etsy Image Agent from a Windows-generated SHA256 hash.

The secret is generated and retained on the Windows tool. Only its 256-bit
one-way digest and public UUID are passed to CAM. Do not use this helper for
Scout agent identities or for existing credentials.
"""
from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .model import EtsySpyAgent, utcnow
from .service import IMAGE_AGENT_NAME_PREFIX


def register_hashed_image_agent(
    db: Session, *,
    agent_id: str,
    token_sha256: str,
    tenant_id: str = "boostgame",
    label: str = "DESKTOP-3FR0LF0",
) -> str:
    # Explicit, operator-owned tenant allowlist: an offline device's digest
    # never grants cross-tenant access, even when the display slug differs.
    if tenant_id not in {"boostgame", "creative-assets-prod"}:
        raise ValueError("unauthorized_tenant")
    try:
        if str(UUID(agent_id, version=4)) != agent_id:
            raise ValueError("invalid_device_uuid")
    except (ValueError, AttributeError):
        raise ValueError("invalid_device_uuid") from None
    if not re.fullmatch(r"[0-9a-f]{64}", token_sha256):
        raise ValueError("invalid_token_hash")
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,48}", label):
        raise ValueError("invalid_device_label")
    name = IMAGE_AGENT_NAME_PREFIX + label
    existing = db.get(EtsySpyAgent, agent_id)
    if existing is not None:
        if (existing.tenant_id != tenant_id or existing.token_hash != token_sha256
            or existing.name != name or not existing.active):
            raise ValueError("device_id_collision_or_revoked")
        return "already_paired"
    duplicate = db.scalar(select(EtsySpyAgent).where(
        EtsySpyAgent.token_hash == token_sha256,
    ))
    if duplicate is not None:
        raise ValueError("duplicate_device_credential")
    db.add(EtsySpyAgent(
        id=agent_id, tenant_id=tenant_id, name=name,
        token_hash=token_sha256, active=True, created_at=utcnow(),
    ))
    db.commit()
    return "paired"
