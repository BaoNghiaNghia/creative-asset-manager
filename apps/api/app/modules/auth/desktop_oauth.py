from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import timedelta
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import update

from app.core.config import Settings, get_settings
from app.modules.auth_persistence.model import DesktopOAuthHandoffModel
from app.modules.auth_persistence.repository import utcnow
from app.modules.auth_persistence.service import auth_repository

_BINDING_RE = re.compile(r"^[a-f0-9]{64}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
DESKTOP_INTENT_PREFIX = "desktop_application_login:"
DESKTOP_HANDOFF_TTL_SECONDS = 300


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def desktop_intent(handoff_id: str) -> str:
    return DESKTOP_INTENT_PREFIX + handoff_id


def handoff_id_from_intent(value: str) -> str | None:
    if not value.startswith(DESKTOP_INTENT_PREFIX):
        return None
    handoff_id = value.removeprefix(DESKTOP_INTENT_PREFIX)
    return handoff_id if len(handoff_id) == 36 else None


def validate_binding(value: str) -> str:
    if not _BINDING_RE.fullmatch(value):
        raise HTTPException(422, detail={"code": "invalid_desktop_binding"})
    return value


def validate_secret(value: str, *, field: str) -> str:
    if not _TOKEN_RE.fullmatch(value):
        raise HTTPException(400, detail={"code": "invalid_" + field})
    return value


def require_desktop_oauth(settings: Settings | None = None) -> None:
    if not (settings or get_settings()).DESKTOP_OAUTH_ENABLED:
        raise HTTPException(503, detail={"code": "desktop_oauth_disabled"})


def start_handoff(*, provider: str, desktop_instance_binding: str) -> str:
    require_desktop_oauth()
    if provider not in {"google", "microsoft"}:
        raise HTTPException(422, detail={"code": "unsupported_provider"})
    binding = validate_binding(desktop_instance_binding)
    launch_token = secrets.token_urlsafe(32)
    with auth_repository() as repository:
        row = DesktopOAuthHandoffModel(
            id=str(uuid4()),
            provider=provider,
            desktop_instance_binding_hash=binding,
            launch_token_hash=digest(launch_token),
            expires_at=utcnow() + timedelta(seconds=DESKTOP_HANDOFF_TTL_SECONDS),
            key_version=repository.cipher.active_version,
        )
        repository.session.add(row)
        repository.audit(
            "desktop_oauth_started",
            tenant_id=None,
            provider=provider,
            actor_id=None,
        )
    return launch_token


def consume_launch_token(launch_token: str) -> DesktopOAuthHandoffModel:
    require_desktop_oauth()
    token = validate_secret(launch_token, field="launch_token")
    with auth_repository() as repository:
        row = repository.session.scalar(
            update(DesktopOAuthHandoffModel)
            .where(
                DesktopOAuthHandoffModel.launch_token_hash == digest(token),
                DesktopOAuthHandoffModel.launch_consumed_at.is_(None),
                DesktopOAuthHandoffModel.expires_at > utcnow(),
            )
            .values(launch_consumed_at=utcnow())
            .returning(DesktopOAuthHandoffModel)
        )
        if row is None:
            raise HTTPException(404, detail={"code": "invalid_or_expired_desktop_launch"})
        return row


def set_browser_binding(*, handoff_id: str, browser_binding: str) -> None:
    with auth_repository() as repository:
        row = repository.session.get(DesktopOAuthHandoffModel, handoff_id)
        if row is None or row.expires_at <= utcnow() or row.launch_consumed_at is None:
            raise HTTPException(400, detail={"code": "invalid_desktop_launch"})
        row.browser_binding_hash = digest(browser_binding)


def complete_callback(
    *,
    handoff_id: str,
    provider: str,
    browser_binding: str,
    pending_payload: dict[str, Any],
) -> str:
    require_desktop_oauth()
    ticket = secrets.token_urlsafe(32)
    with auth_repository() as repository:
        row = repository.session.get(DesktopOAuthHandoffModel, handoff_id)
        if (
            row is None
            or row.provider != provider
            or row.expires_at <= utcnow()
            or row.launch_consumed_at is None
            or row.ticket_hash is not None
            or not row.browser_binding_hash
            or not secrets.compare_digest(row.browser_binding_hash, digest(browser_binding))
        ):
            raise HTTPException(400, detail={"code": "invalid_desktop_callback"})
        encrypted = repository.cipher.encrypt(
            json.dumps(pending_payload, separators=(",", ":")),
            aad="cam-desktop-oauth:" + row.id,
        )
        row.pending_payload_ciphertext = encrypted.ciphertext
        row.key_version = encrypted.key_version
        row.ticket_hash = digest(ticket)
        row.callback_completed_at = utcnow()
        repository.audit(
            "desktop_oauth_callback_completed",
            tenant_id=None,
            provider=provider,
            actor_id=None,
        )
    return ticket


def claim_handoff(*, ticket: str, desktop_instance_nonce: str) -> tuple[str, dict[str, Any]]:
    require_desktop_oauth()
    raw_ticket = validate_secret(ticket, field="ticket")
    if not _TOKEN_RE.fullmatch(desktop_instance_nonce):
        raise HTTPException(400, detail={"code": "invalid_desktop_nonce"})
    with auth_repository() as repository:
        row = repository.session.scalar(
            update(DesktopOAuthHandoffModel)
            .where(
                DesktopOAuthHandoffModel.ticket_hash == digest(raw_ticket),
                DesktopOAuthHandoffModel.consumed_at.is_(None),
                DesktopOAuthHandoffModel.expires_at > utcnow(),
                DesktopOAuthHandoffModel.callback_completed_at.is_not(None),
                DesktopOAuthHandoffModel.desktop_instance_binding_hash == digest(desktop_instance_nonce),
            )
            .values(consumed_at=utcnow())
            .returning(DesktopOAuthHandoffModel)
        )
        if row is None:
            raise HTTPException(400, detail={"code": "invalid_or_expired_desktop_ticket"})
        plaintext = repository.cipher.decrypt(
            row.pending_payload_ciphertext,
            key_version=row.key_version,
            aad="cam-desktop-oauth:" + row.id,
        )
        if not plaintext:
            raise HTTPException(400, detail={"code": "invalid_desktop_handoff"})
        repository.audit(
            "desktop_oauth_redeemed",
            tenant_id=None,
            provider=row.provider,
            actor_id=None,
        )
        return row.provider, json.loads(plaintext)
