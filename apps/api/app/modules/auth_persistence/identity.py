from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.auth_persistence.model import UserIdentityModel, UserModel

SUPPORTED_PROVIDERS = frozenset({"google", "microsoft"})
MAX_METADATA_BYTES = 4096
MAX_METADATA_STRING = 512
SAFE_METADATA_KEYS = {
    "google": frozenset({"email_verified", "hosted_domain", "locale"}),
    "microsoft": frozenset(
        {"account_enabled", "preferred_language", "user_principal_name"}
    ),
}


class ApplicationUserInactiveError(PermissionError):
    """The external identity is valid but its application user is not active."""


class IdentityConflictError(ValueError):
    """An external identity is already linked to another application user."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(value: str | None) -> str | None:
    normalized = (value or "").strip().casefold()
    return normalized[:512] or None


def safe_avatar_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))[:2048]


def bounded_provider_metadata(
    provider: str, value: Mapping[str, Any] | None
) -> dict:
    allowed = SAFE_METADATA_KEYS.get(provider, frozenset())
    result: dict[str, str | bool | int | float | None] = {}
    for key in sorted(value or {}):
        if key not in allowed:
            continue
        item = (value or {})[key]
        if item is None or isinstance(item, (bool, int, float)):
            result[key] = item
        elif isinstance(item, str):
            result[key] = item[:MAX_METADATA_STRING]
    if len(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
    ) <= MAX_METADATA_BYTES:
        return result
    bounded: dict[str, Any] = {}
    for key, item in result.items():
        candidate = {**bounded, key: item}
        if len(
            json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode()
        ) > MAX_METADATA_BYTES:
            break
        bounded[key] = item
    return bounded


class IdentityResolutionService:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def normalize_provider(provider: str) -> str:
        value = provider.strip().casefold()
        if value not in SUPPORTED_PROVIDERS:
            raise ValueError("unsupported identity provider")
        return value

    @staticmethod
    def normalize_subject(provider_subject: str) -> str:
        value = provider_subject.strip()
        if not value:
            raise ValueError("provider subject must not be empty")
        if len(value) > 512:
            raise ValueError("provider subject is too long")
        return value

    def find_by_provider_subject(
        self, provider: str, provider_subject: str
    ) -> UserIdentityModel | None:
        return self.session.scalar(
            select(UserIdentityModel).where(
                UserIdentityModel.provider == self.normalize_provider(provider),
                UserIdentityModel.provider_subject
                == self.normalize_subject(provider_subject),
            )
        )

    def create_user_from_identity(
        self,
        *,
        provider: str,
        provider_subject: str,
        provider_email: str | None = None,
        display_name: str | None = None,
        avatar_url: str | None = None,
        provider_tenant_id: str | None = None,
        provider_metadata: Mapping[str, Any] | None = None,
    ) -> tuple[UserModel, UserIdentityModel]:
        normalized_provider = self.normalize_provider(provider)
        email = normalize_email(provider_email)
        user = UserModel(
            primary_email=email,
            display_name=(display_name or "").strip()[:512] or None,
            avatar_url=safe_avatar_url(avatar_url),
            status="active",
        )
        self.session.add(user)
        self.session.flush()
        identity = UserIdentityModel(
            user_id=user.id,
            provider=normalized_provider,
            provider_subject=self.normalize_subject(provider_subject),
            provider_email=email,
            provider_tenant_id=(provider_tenant_id or "").strip()[:512] or None,
            provider_metadata_json=bounded_provider_metadata(
                normalized_provider, provider_metadata
            ),
        )
        self.session.add(identity)
        self.session.flush()
        return user, identity

    def link_identity_to_user(
        self,
        *,
        user_id: str,
        provider: str,
        provider_subject: str,
        provider_email: str | None = None,
        provider_tenant_id: str | None = None,
        provider_metadata: Mapping[str, Any] | None = None,
    ) -> UserIdentityModel:
        user = self.session.get(UserModel, user_id)
        if user is None:
            raise LookupError("application user not found")
        self.require_active(user)
        existing = self.find_by_provider_subject(provider, provider_subject)
        if existing is not None:
            if existing.user_id != user_id:
                raise IdentityConflictError("identity is linked to another user")
            return existing
        identity = UserIdentityModel(
            user_id=user_id,
            provider=self.normalize_provider(provider),
            provider_subject=self.normalize_subject(provider_subject),
            provider_email=normalize_email(provider_email),
            provider_tenant_id=(provider_tenant_id or "").strip()[:512] or None,
            provider_metadata_json=bounded_provider_metadata(
                self.normalize_provider(provider), provider_metadata
            ),
        )
        self.session.add(identity)
        self.session.flush()
        return identity

    def update_safe_profile_fields(
        self,
        user: UserModel,
        identity: UserIdentityModel,
        *,
        provider_email: str | None,
        display_name: str | None,
        avatar_url: str | None,
        provider_tenant_id: str | None,
        provider_metadata: Mapping[str, Any] | None,
    ) -> None:
        email = normalize_email(provider_email)
        identity.provider_email = email
        identity.provider_tenant_id = (
            (provider_tenant_id or "").strip()[:512] or None
        )
        identity.provider_metadata_json = bounded_provider_metadata(
            identity.provider, provider_metadata
        )
        identity.updated_at = utcnow()
        # Email is a display/search attribute only; it never selects or links.
        user.primary_email = email
        user.display_name = (
            (display_name or "").strip()[:512] or user.display_name
        )
        user.avatar_url = safe_avatar_url(avatar_url) or user.avatar_url
        user.updated_at = utcnow()

    def record_login(
        self, user: UserModel, identity: UserIdentityModel
    ) -> None:
        self.require_active(user)
        now = utcnow()
        user.last_login_at = now
        user.updated_at = now
        identity.last_login_at = now
        identity.updated_at = now
        self.session.flush()

    def resolve_login(
        self,
        *,
        provider: str,
        provider_subject: str,
        provider_email: str | None = None,
        display_name: str | None = None,
        avatar_url: str | None = None,
        provider_tenant_id: str | None = None,
        provider_metadata: Mapping[str, Any] | None = None,
    ) -> tuple[UserModel, UserIdentityModel]:
        user, identity, _created = self.resolve_login_with_status(
            provider=provider,
            provider_subject=provider_subject,
            provider_email=provider_email,
            display_name=display_name,
            avatar_url=avatar_url,
            provider_tenant_id=provider_tenant_id,
            provider_metadata=provider_metadata,
        )
        return user, identity

    def resolve_login_with_status(
        self,
        *,
        provider: str,
        provider_subject: str,
        provider_email: str | None = None,
        display_name: str | None = None,
        avatar_url: str | None = None,
        provider_tenant_id: str | None = None,
        provider_metadata: Mapping[str, Any] | None = None,
    ) -> tuple[UserModel, UserIdentityModel, bool]:
        normalized_provider = self.normalize_provider(provider)
        normalized_subject = self.normalize_subject(provider_subject)
        identity = self.find_by_provider_subject(
            normalized_provider, normalized_subject
        )
        created = False
        if identity is None:
            try:
                with self.session.begin_nested():
                    user, identity = self.create_user_from_identity(
                        provider=normalized_provider,
                        provider_subject=normalized_subject,
                        provider_email=provider_email,
                        display_name=display_name,
                        avatar_url=avatar_url,
                        provider_tenant_id=provider_tenant_id,
                        provider_metadata=provider_metadata,
                    )
                    created = True
            except IntegrityError:
                identity = self.find_by_provider_subject(
                    normalized_provider, normalized_subject
                )
                if identity is None:
                    raise
                user = self.session.get(UserModel, identity.user_id)
        else:
            user = self.session.get(UserModel, identity.user_id)
        if user is None:
            raise LookupError("identity application user not found")
        self.require_active(user)
        self.update_safe_profile_fields(
            user,
            identity,
            provider_email=provider_email,
            display_name=display_name,
            avatar_url=avatar_url,
            provider_tenant_id=provider_tenant_id,
            provider_metadata=provider_metadata,
        )
        self.record_login(user, identity)
        return user, identity, created

    @staticmethod
    def require_active(user: UserModel) -> None:
        if user.status != "active":
            raise ApplicationUserInactiveError("application user is not active")
