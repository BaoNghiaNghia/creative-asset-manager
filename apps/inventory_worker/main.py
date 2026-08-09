#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1] / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.modules.inventory.worker import run_inventory_worker  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(run_inventory_worker())
