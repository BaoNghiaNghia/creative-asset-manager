"""Read-only Phase 5A activation gate for production video CDN rollout.

This command combines the database/config preflight with both signed Worker HEAD
probes. It never enables the runtime toggle, mutates R2, or prints credentials,
signed URLs, tenant IDs, asset IDs, source IDs, or object keys.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from typing import Callable

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import (
    SessionLocal,
    engine,
    resolve_database_url,
    validate_alembic_head,
)
from app.modules.video_cache.guard import VIDEO_DELIVERY_GUARD
from app.modules.video_cache.runtime import (
    VideoDeliveryRuntimeService,
    VideoDeliveryRuntimeUnavailable,
)
from app.operations.video_delivery_preflight import (
    PreflightCheck,
    probe_ready_video_ticket,
    probe_worker_ticket,
    video_delivery_preflight,
)


def activation_readiness(
    session: Session,
    settings: Settings,
    *,
    require_production: bool = True,
    allow_global_rollout: bool = False,
    worker_probe: Callable[[Settings], PreflightCheck] = probe_worker_ticket,
    ready_probe: Callable[[Session, Settings], PreflightCheck] = probe_ready_video_ticket,
) -> dict[str, object]:
    checks = video_delivery_preflight(
        session,
        settings,
        require_production=require_production,
        require_runtime_off=True,
        require_ready=True,
        require_canary_scope=not allow_global_rollout,
    )
    checks.append(worker_probe(settings))
    checks.append(ready_probe(session, settings))

    try:
        runtime = VideoDeliveryRuntimeService(session, settings).get_status()
    except VideoDeliveryRuntimeUnavailable:
        runtime = None

    guard = VIDEO_DELIVERY_GUARD.snapshot(settings)
    ready = bool(
        runtime is not None
        and not runtime["runtime_enabled"]
        and runtime["can_enable"]
        and all(item.ok for item in checks)
    )
    return {
        "ready_to_enable": ready,
        "next_action": (
            "enable the persisted runtime toggle with an audited operator reason"
            if ready
            else "keep runtime delivery disabled and resolve failed checks"
        ),
        "runtime": runtime,
        "guard": guard,
        "checks": [asdict(item) for item in checks],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Phase 5A production video CDN activation gate"
    )
    parser.add_argument(
        "--allow-non-production",
        action="store_true",
        help="allow a staging/development dry run instead of requiring APP_ENV=production",
    )
    parser.add_argument(
        "--allow-global-rollout",
        action="store_true",
        help="accept explicit global rollout config instead of requiring tenant canary scope",
    )
    args = parser.parse_args(argv)

    settings = Settings()
    alembic_check: PreflightCheck
    try:
        validate_alembic_head(
            engine,
            database_url=resolve_database_url(settings),
        )
        alembic_check = PreflightCheck(
            code="alembic_head",
            ok=True,
            detail="database schema is at the single expected Alembic head",
        )
    except Exception:
        alembic_check = PreflightCheck(
            code="alembic_head",
            ok=False,
            detail="database schema is unavailable or not at the expected Alembic head",
        )

    with SessionLocal() as session:
        result = activation_readiness(
            session,
            settings,
            require_production=not args.allow_non_production,
            allow_global_rollout=args.allow_global_rollout,
        )

    checks = [asdict(alembic_check), *result["checks"]]
    ready = bool(alembic_check.ok and result["ready_to_enable"])
    payload = {
        **result,
        "ready_to_enable": ready,
        "checks": checks,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
