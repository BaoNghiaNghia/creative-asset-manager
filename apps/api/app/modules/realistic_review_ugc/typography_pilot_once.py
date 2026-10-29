"""Operator-invoked G3 pilot tick. Disabled at multiple independent boundaries.

This is NOT a scheduler, registered CAM worker, daemon, API endpoint or systemd
service. Importing it is side-effect free. Running without --execute is a
strict dry-run: no DB, Managed Storage or AI provider construction.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Sequence

from app.core.config import get_settings, Settings
from app.modules.realistic_review_ugc.typography_visual_analyzer import (
    TypographyVisualAnalysisRunner, tenant_can_analyze,
)
from app.modules.storage.provider_factory import build_managed_storage_provider
from app.providers.storage.unconfigured import UnconfiguredAssetStorageProvider
from app.providers.ai.factory import build_ai_provider_registry
from app.domain.providers.registry import AiProviderUnavailableError


def _emit(status: str, *, provider_calls: int = 0, latency_ms: int = 0) -> None:
    # No image, keyword, token, tenant ID, storage path or provider response
    # is printed; only bounded operational telemetry for a single tick.
    print(json.dumps({
        "status": status, "provider_calls": provider_calls,
        "latency_ms": latency_ms,
    }, sort_keys=True))


async def _execute_once(tenant_id: str, worker_id: str, settings: Settings) -> int:
    if not getattr(settings, "TYPOGRAPHY_V2_PILOT_WORKER_ENABLED", False):
        _emit("pilot_worker_disabled")
        return 0
    if not tenant_can_analyze(tenant_id):
        _emit("tenant_not_enabled")
        return 0
    if not getattr(settings, "TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED", False):
        _emit("rollback_gate_unverified")
        return 0
    storage = build_managed_storage_provider(settings)
    if isinstance(storage, UnconfiguredAssetStorageProvider):
        _emit("managed_storage_unavailable")
        return 2

    # Load the SQLAlchemy engine only after the feature and rollback gates pass.
    from app.core.database import SessionLocal
    registry = build_ai_provider_registry(settings, session_factory=SessionLocal)
    try:
        try:
            provider = registry.require("gemini")
        except AiProviderUnavailableError:
            _emit("vision_provider_unavailable")
            return 2
        runner = TypographyVisualAnalysisRunner(
            session_factory=SessionLocal,
            storage=storage,
            provider=provider,
        )
        # One eligible job maximum per explicit command; never loop or poll.
        result = await runner.tick(tenant_id=tenant_id, worker_id=worker_id,
                                   concurrency=1)
        _emit(result.status, provider_calls=result.provider_calls,
              latency_ms=result.latency_ms)
        return 0 if result.status in {
            "completed", "no_eligible_job", "feature_disabled",
            "needs_review", "queued",
        } else 2
    finally:
        await registry.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One guarded Typography V2 pilot analyzer attempt (OFF by default).",
    )
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--execute", action="store_true",
                        help="Explicitly permit one provider-backed attempt (requires BOTH server flags).")
    args = parser.parse_args(argv)
    if not args.execute:
        _emit("dry_run_only")
        return 0
    if not args.tenant_id or len(args.tenant_id) > 255 or not args.tenant_id.strip():
        _emit("invalid_tenant")
        return 2
    settings = get_settings()
    # Check before creating any DB, storage or provider objects.
    if not (settings.TYPOGRAPHY_V2_ENABLED and
            getattr(settings, "TYPOGRAPHY_V2_PILOT_WORKER_ENABLED", False)):
        _emit("pilot_worker_disabled")
        return 0
    if args.tenant_id not in {
        item.strip() for item in settings.TYPOGRAPHY_V2_CANARY_TENANT_IDS.split(",")
        if item.strip()
    }:
        _emit("tenant_not_enabled")
        return 0
    if not getattr(settings, "TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED", False):
        _emit("rollback_gate_unverified")
        return 0
    worker_id = f"typo-pilot-{os.getpid()}"
    return asyncio.run(_execute_once(args.tenant_id, worker_id, settings))


if __name__ == "__main__":
    raise SystemExit(main())
