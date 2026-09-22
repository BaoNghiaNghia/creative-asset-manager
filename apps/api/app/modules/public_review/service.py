from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe
from uuid import uuid4

from app.modules.public_review.model import PublicShareModel, PublicShareSessionModel, utcnow
from app.modules.public_review.repository import PublicReviewRepository
from app.modules.public_review.schema import extract_plain_text


DEFAULT_SHARE_TTL = timedelta(days=7)


def sha256_digest(raw_value: str) -> str:
    if not isinstance(raw_value, str) or not raw_value:
        raise ValueError("secret value is required")
    return sha256(raw_value.encode("utf-8")).hexdigest()


def normalize_display_name(value: str) -> str:
    normalized = " ".join(value.split())
    if not 1 <= len(normalized) <= 160:
        raise ValueError("display name must be between 1 and 160 characters")
    return normalized


def validate_anchors(anchor_x: float | None, anchor_y: float | None) -> None:
    if (anchor_x is None) != (anchor_y is None):
        raise ValueError("annotation anchors must be provided as a pair")
    if anchor_x is not None and not (0 <= anchor_x <= 1 and 0 <= anchor_y <= 1):
        raise ValueError("annotation anchors must be normalized to [0, 1]")


class PublicReviewService:
    """Phase-1 foundation service. It does not expose HTTP or issue browser cookies."""

    def __init__(self, repository: PublicReviewRepository, now=utcnow):
        self.repository = repository
        self._now = now

    def create_share(self, *, tenant_id: str, public_id: str, name: str, raw_secret: str, created_by: str, **values) -> PublicShareModel:
        normalized_name = " ".join(name.split())
        if not 1 <= len(normalized_name) <= 255:
            raise ValueError("share name must be between 1 and 255 characters")
        return self.repository.create_share(tenant_id=tenant_id, public_id=public_id, name=normalized_name, secret_digest=sha256_digest(raw_secret), created_by=created_by, **values)

    def verify_share_secret(self, public_id: str, raw_secret: str, now: datetime | None = None) -> PublicShareModel:
        row = self.repository.get_share_by_secret_digest(public_id, sha256_digest(raw_secret))
        if row is None or not row.is_active_at(now or self._now()):
            raise LookupError("public share is unavailable")
        return row

    def create_guest(self, *, tenant_id: str, share_id: str, display_name: str):
        return self.repository.create_guest(tenant_id=tenant_id, share_id=share_id, display_name=normalize_display_name(display_name))

    def create_session(self, *, tenant_id: str, share_id: str, raw_session_token: str, expires_at: datetime, guest_id: str | None = None) -> PublicShareSessionModel:
        share = self.repository.get_share(tenant_id, share_id)
        now = self._now()
        if share is None or not share.is_active_at(now):
            raise LookupError("public share is unavailable")
        if expires_at <= now:
            raise ValueError("session expiry must be in the future")
        if share.expires_at is not None and expires_at > share.expires_at:
            raise ValueError("session expiry cannot outlive the share")
        return self.repository.create_session(tenant_id=tenant_id, share_id=share_id, guest_id=guest_id, session_digest=sha256_digest(raw_session_token), expires_at=expires_at)

    def resolve_session(self, *, tenant_id: str, raw_session_token: str, now: datetime | None = None) -> PublicShareSessionModel:
        row = self.repository.get_session_by_digest(tenant_id, sha256_digest(raw_session_token))
        instant = now or self._now()
        if row is None or not row.is_active_at(instant):
            raise LookupError("public share session is unavailable")
        share = self.repository.get_share(tenant_id, row.share_id)
        if share is None or not share.is_active_at(instant):
            raise LookupError("public share is unavailable")
        row.last_seen_at = instant
        self.repository.session.flush()
        return row

    def create_annotation(self, *, anchor_x: float | None, anchor_y: float | None, content_json: dict, **values):
        validate_anchors(anchor_x, anchor_y)
        # Plain text is derived only from the structured document; client input is ignored.
        values.pop("plain_text", None)
        return self.repository.create_annotation(
            anchor_x=anchor_x,
            anchor_y=anchor_y,
            content_json=content_json,
            plain_text=extract_plain_text(content_json),
            **values,
        )


    @staticmethod
    def _validate_expiry(expires_at: datetime | None, now: datetime) -> None:
        if expires_at is not None and (expires_at.tzinfo is None or expires_at.astimezone(timezone.utc) <= now):
            raise ValueError("share expiry must be in the future")

    @staticmethod
    def _new_secret() -> str:
        return token_urlsafe(32)

    def create_managed_share(self, *, tenant_id: str, actor_id: str, name: str, scopes: list[dict], allow_comments: bool = True, allow_download: bool = False, expires_at: datetime | None = None):
        now = self._now()
        expires_at = expires_at or (now + DEFAULT_SHARE_TTL)
        self._validate_expiry(expires_at, now)
        if not scopes:
            raise ValueError("at least one share scope is required")
        self.repository.validate_management_scopes(tenant_id, scopes)
        raw_secret = self._new_secret()
        share = self.create_share(tenant_id=tenant_id, public_id=uuid4().hex, name=name, raw_secret=raw_secret, created_by=actor_id, allow_comments=allow_comments, allow_download=allow_download, expires_at=expires_at)
        self.repository.replace_scopes(tenant_id, share.id, scopes)
        self.repository.audit_share_event(tenant_id=tenant_id, actor_id=actor_id, action="public_review_share_created", share_id=share.id, detail={"scope_count": len(scopes)})
        return share, raw_secret

    def update_managed_share(self, *, tenant_id: str, actor_id: str, share_id: str, changes: dict, scopes: list[dict] | None = None):
        share = self.repository.get_share(tenant_id, share_id)
        if share is None:
            raise LookupError("public share not found")
        if "name" in changes:
            changes["name"] = " ".join(changes["name"].split())
            if not changes["name"]:
                raise ValueError("share name must be between 1 and 255 characters")
        if "expires_at" in changes:
            self._validate_expiry(changes["expires_at"], self._now())
        if changes:
            share = self.repository.update_share(tenant_id, share_id, **changes)
            self.repository.audit_share_event(tenant_id=tenant_id, actor_id=actor_id, action="public_review_share_updated", share_id=share_id, detail={"fields": sorted(changes)})
        if scopes is not None:
            if not scopes:
                raise ValueError("at least one share scope is required")
            self.repository.validate_management_scopes(tenant_id, scopes)
            self.repository.replace_scopes(tenant_id, share_id, scopes)
            self.repository.audit_share_event(tenant_id=tenant_id, actor_id=actor_id, action="public_review_share_scopes_updated", share_id=share_id, detail={"scope_count": len(scopes)})
        return share

    def rotate_managed_share_secret(self, *, tenant_id: str, actor_id: str, share_id: str):
        share = self.repository.get_share(tenant_id, share_id)
        if share is None or not share.is_active_at(self._now()):
            raise LookupError("public share is unavailable")
        now = self._now()
        raw_secret = self._new_secret()
        share = self.repository.update_share(
            tenant_id,
            share_id,
            secret_digest=sha256_digest(raw_secret),
            expires_at=now + DEFAULT_SHARE_TTL,
        )
        self.repository.revoke_sessions(tenant_id, share_id, now)
        self.repository.audit_share_event(tenant_id=tenant_id, actor_id=actor_id, action="public_review_share_secret_rotated", share_id=share_id)
        return share, raw_secret

    def revoke_managed_share(self, *, tenant_id: str, actor_id: str, share_id: str):
        share = self.repository.revoke_share(tenant_id, share_id, self._now())
        self.repository.audit_share_event(tenant_id=tenant_id, actor_id=actor_id, action="public_review_share_revoked", share_id=share_id)
        return share
