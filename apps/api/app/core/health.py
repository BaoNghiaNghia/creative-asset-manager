from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings
from app.core.database import validate_database_connection


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    status_code: int
    payload: dict[str, Any]


def postgresql_is_ready() -> bool:
    try:
        validate_database_connection()
    except Exception:
        return False
    return True


def elasticsearch_is_ready(settings: Settings) -> bool:
    if not settings.ELASTICSEARCH_URL:
        return False
    try:
        with httpx.Client(
            base_url=settings.ELASTICSEARCH_URL.rstrip("/"),
            timeout=settings.HEALTHCHECK_TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = client.get("/_cluster/health", params={"local": "true"})
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return False
    return (
        isinstance(payload, dict)
        and payload.get("status") in {"green", "yellow"}
        and payload.get("timed_out") is not True
    )


def readiness_report(settings: Settings) -> ReadinessResult:
    dependencies = {
        "postgresql": "available" if postgresql_is_ready() else "unavailable",
        "elasticsearch": "disabled",
    }
    elasticsearch_required = settings.elasticsearch_readiness_required
    if elasticsearch_required:
        dependencies["elasticsearch"] = (
            "available" if elasticsearch_is_ready(settings) else "unavailable"
        )

    if dependencies["postgresql"] != "available":
        return ReadinessResult(
            status_code=503,
            payload={"status": "not_ready", "dependencies": dependencies},
        )
    if elasticsearch_required and dependencies["elasticsearch"] != "available":
        return ReadinessResult(
            status_code=503,
            payload={"status": "degraded", "dependencies": dependencies},
        )
    return ReadinessResult(
        status_code=200,
        payload={"status": "ready", "dependencies": dependencies},
    )
