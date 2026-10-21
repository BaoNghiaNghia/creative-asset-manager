"""Controlled operator command to register the three confirmed Catinat stock areas."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from app.core.database import SessionLocal
from app.modules.auth_persistence import model as _auth_models  # noqa: F401
from app.modules.inventory.daily.catinat_location_bootstrap import (
    CONFIRMED_LOCATIONS, bootstrap_confirmed_locations,
)
from app.modules.inventory.persistence_model import InventorySettingsModel


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    options = parser.parse_args()
    with SessionLocal.begin() as session:
        configured = list(session.scalars(select(InventorySettingsModel).where(
            InventorySettingsModel.enabled.is_(True),
            InventorySettingsModel.daily_sheet_automation_enabled.is_(True),
        )))
        if len(configured) != 1:
            raise ValueError("expected_exactly_one_enabled_inventory_tenant")
        settings = configured[0]
        config = settings.daily_sheet_config_json
        if not isinstance(config, dict) or config.get("version") != 4:
            raise ValueError("expected_catinat_v4_configuration")
        allowed = (config.get("source") or {}).get("allowed_sheets")
        if not isinstance(allowed, list) or not all(name in allowed for _, name in CONFIRMED_LOCATIONS):
            raise ValueError("confirmed_stock_areas_not_authorized")
        report = bootstrap_confirmed_locations(session, settings.tenant_id, apply=options.apply)
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
