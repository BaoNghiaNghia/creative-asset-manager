from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.modules.external_ingestion.model import ExternalApiCredentialModel
from app.modules.external_ingestion.security import sensitive_url_cipher
from app.modules.external_ingestion.repository import (
    ExternalIngestionRepository,
    RateLimitExceededError,
)

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class ExternalApiContext:
    credential: ExternalApiCredentialModel
    repository: ExternalIngestionRepository

    @property
    def tenant_id(self) -> str:
        return self.credential.tenant_id

    @property
    def source_id(self) -> str:
        return self.credential.external_source_id


def require_external_ingestion_enabled(
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.EXTERNAL_INGESTION_API_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")


def authenticate_external_api(
    response: Response,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: Session = Depends(get_db),
    _enabled: None = Depends(require_external_ingestion_enabled),
    settings: Settings = Depends(get_settings),
) -> ExternalApiContext:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Missing API bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    repository = ExternalIngestionRepository(
        session,
        url_cipher=sensitive_url_cipher(settings),
        url_retention_hours=settings.RETENTION_INGESTION_URL_HOURS,
    )
    credential = repository.authenticate(credentials.credentials)
    if credential is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid API bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    source = repository.get_source(credential.tenant_id, credential.external_source_id)
    if source is None or source.source_type != "external_api":
        raise HTTPException(status_code=403, detail="API source is not authorized")
    try:
        used, remaining = repository.consume_rate_limit(credential)
        session.commit()
    except RateLimitExceededError as exc:
        session.rollback()
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    response.headers["X-RateLimit-Limit"] = str(credential.rate_limit_per_minute)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Used"] = str(used)
    return ExternalApiContext(credential, repository)
