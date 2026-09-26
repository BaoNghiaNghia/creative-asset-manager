from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.pipeline.attention_recovery import PipelineAttentionRecovery
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.storage.managed_oauth import (
    ManagedStorageCredentialUnavailableError,
    ManagedStorageCredentialValidationError,
    check_managed_storage_refresh_token,
    mark_managed_storage_reconnect_required,
    resolve_managed_storage_credential,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--apply", action="store_true")
    value.add_argument("--limit", type=int, default=5000)
    return value


def tenant_ids(session) -> list[str]:
    values = set(session.scalars(select(AssetPipelineModel.tenant_id).distinct()))
    values.update(session.scalars(select(ProcessingJobModel.tenant_id).distinct()))
    return sorted(str(value) for value in values if value)


def reconcile_managed_storage(
    settings, tenant_id: str | None, *, mark_reconnect: bool
) -> str:
    credential = resolve_managed_storage_credential(settings)
    if not tenant_id or not credential.refresh_token:
        return "unconfigured_or_reconnect_required"
    try:
        asyncio.run(check_managed_storage_refresh_token(
            settings,
            credential.refresh_token,
            tenant_id=tenant_id,
            initiating_user_id="pipeline-attention-repair",
            save=False,
        ))
    except ManagedStorageCredentialValidationError:
        if not mark_reconnect:
            return "invalid_refresh_token"
        updated = mark_managed_storage_reconnect_required(
            settings,
            code="google_refresh_token_rejected",
        )
        return f"reconnect_required:{updated}"
    except ManagedStorageCredentialUnavailableError:
        return "validation_temporarily_unavailable"
    return "healthy"


def main() -> int:
    args = parser().parse_args()
    if args.limit < 1 or args.limit > 20000:
        raise SystemExit("--limit must be between 1 and 20000")
    settings = get_settings()
    with SessionLocal() as session:
        tenants = tenant_ids(session)
        storage_status = reconcile_managed_storage(
            settings,
            tenants[0] if tenants else None,
            mark_reconnect=args.apply,
        )
        print(f"PIPELINE_ATTENTION_REPAIR managed_storage={storage_status}")
        total = {
            "storage_pipelines": 0,
            "analysis_jobs": 0,
            "projection_jobs": 0,
            "skipped_missing_identity": 0,
            "skipped_no_profile": 0,
        }
        for tenant_id in tenants:
            recovery = PipelineAttentionRecovery(session, settings)
            result = (
                recovery.apply(tenant_id, limit=args.limit)
                if args.apply
                else recovery.preview(tenant_id)
            )
            document = result.document()
            for key in total:
                total[key] += int(document.get(key, 0))
            print(
                "PIPELINE_ATTENTION_REPAIR "
                f"mode={'apply' if args.apply else 'preview'} tenant=[redacted] "
                + " ".join(f"{key}={value}" for key, value in document.items())
            )
        if args.apply:
            session.commit()
        else:
            session.rollback()
        print(
            "PIPELINE_ATTENTION_REPAIR "
            f"mode={'apply' if args.apply else 'preview'} tenants={len(tenants)} "
            + " ".join(f"{key}={value}" for key, value in total.items())
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
