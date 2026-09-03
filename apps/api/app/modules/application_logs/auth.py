from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.application_logs.model import LogApplicationModel
from app.modules.application_logs.repository import ApplicationLogRepository

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class LogApplicationContext:
    application: LogApplicationModel
    repository: ApplicationLogRepository


def authenticate_log_application(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), session: Session = Depends(get_db)) -> LogApplicationContext:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise HTTPException(status_code=401, detail="Missing API bearer token", headers={"WWW-Authenticate": "Bearer"})
    repository = ApplicationLogRepository(session); application = repository.authenticate(credentials.credentials)
    if application is None: raise HTTPException(status_code=401, detail="Invalid API bearer token", headers={"WWW-Authenticate": "Bearer"})
    return LogApplicationContext(application=application, repository=repository)
