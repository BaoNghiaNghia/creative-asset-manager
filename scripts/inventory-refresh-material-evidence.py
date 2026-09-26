#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import select

API_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.inventory.material_evidence import MaterialCandidateEvidenceService
from app.modules.inventory.persistence_model import InventorySettingsModel


def main() -> int:
    settings = get_settings()
    with SessionLocal() as session:
        query = select(InventorySettingsModel.tenant_id).where(
            InventorySettingsModel.enabled.is_(True),
            InventorySettingsModel.daily_sheet_automation_enabled.is_(True),
        )
        if settings.inventory_tenant_allowlist:
            query = query.where(
                InventorySettingsModel.tenant_id.in_(
                    list(settings.inventory_tenant_allowlist)
                )
            )
        tenants = list(session.scalars(query))

    service = MaterialCandidateEvidenceService(SessionLocal)
    total = 0
    status_totals: dict[str, int] = {}
    for tenant_id in tenants:
        result = service.refresh_pending(tenant_id)
        updated = int(result.get("updated") or 0)
        total += updated
        for status, count in dict(result.get("statuses") or {}).items():
            status_totals[status] = status_totals.get(status, 0) + int(count)
        status_summary = ",".join(
            f"{status}:{count}"
            for status, count in sorted(dict(result.get("statuses") or {}).items())
        ) or "none"
        print(
            "INVENTORY_MATERIAL_EVIDENCE_REFRESH "
            f"tenant={tenant_id} updated={updated} statuses={status_summary}"
        )
    total_summary = ",".join(
        f"{status}:{count}" for status, count in sorted(status_totals.items())
    ) or "none"
    print(
        "INVENTORY_MATERIAL_EVIDENCE_REFRESH "
        f"tenants={len(tenants)} updated={total} statuses={total_summary}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
